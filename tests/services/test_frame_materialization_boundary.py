from __future__ import annotations
# pyright: reportPrivateUsage=false

import ast
from pathlib import Path
from datetime import timedelta
from django.utils import timezone
from unittest.mock import patch
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIRequestFactory, force_authenticate

from endoreg_db.models import Center, Frame, VideoFile
from endoreg_db.models.media.operation_lease import MediaOperationLease
from endoreg_db.services.video_files.frames import (
    extract_video_frames,
    extract_video_frame_range,
)
from endoreg_db.utils.frame_stream import EncodedFrameSample
from endoreg_db.views.media.frame_media import FrameStreamView, DecodedFrameStreamView
from tests.helpers.video_timeline import constant_rate_video_metadata
from endoreg_db.services.media.state import update_media_state


def test_frame_file_encoder_calls_are_export_owned() -> None:
    root = Path(__file__).resolve().parents[2]
    permitted = {
        "endoreg_db/export/frames/export_frames_with_labels.py",
        "endoreg_db/utils/video/frame_extraction.py",
        "endoreg_db/utils/__init__.py",
    }
    writers = {
        "extract_frames",
        "extract_frame_range",
        "extract_frames_by_presentation_timestamp",
    }
    violations: list[str] = []
    for path in (root / "endoreg_db").rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if relative in permitted or "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        aliases = {
            item.asname or item.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module
            in {
                "endoreg_db.utils.ffmpeg_wrapper",
                "endoreg_db.utils.video.frame_extraction",
                "endoreg_db.utils",
            }
            for item in node.names
            if item.name in writers
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if (isinstance(node.func, ast.Name) and node.func.id in aliases) or (
                isinstance(node.func, ast.Attribute) and node.func.attr in writers
            ):
                violations.append(f"{relative}:{node.lineno}")
    assert not violations, f"Frame materialization outside export: {violations}"


@pytest.mark.parametrize("range_only", [False, True])
def test_retired_cache_entrypoints_fail_before_side_effects(range_only: bool) -> None:
    video = VideoFile()
    with pytest.raises(RuntimeError, match="export-only"):
        if range_only:
            extract_video_frame_range(video, start_frame=0, end_frame=1)
        else:
            extract_video_frames(video)


@pytest.mark.django_db
@pytest.mark.parametrize("view_class", [FrameStreamView, DecodedFrameStreamView])
@pytest.mark.parametrize("busy", [False, True])
def test_annotation_stream_decodes_processed_without_frame_files(
    view_class: type[DecodedFrameStreamView], tmp_path: Path, busy: bool
) -> None:
    center = Center.objects.create(name="stream-boundary")
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash="stream-boundary",
        frame_count=20,
        frame_dir=str(tmp_path / "absent"),
        processed_file="processed/video.mp4",
    )
    state = video.get_or_create_state()
    update_media_state(state, changes={"anonymized": True})
    frame = Frame.objects.create(
        video=video, frame_number=7, timestamp=0.237, is_extracted=False
    )
    if busy:
        MediaOperationLease.objects.create(
            video=video,
            lease_type="transcode",
            expires_at=timezone.now() + timedelta(minutes=5),
        )
    user = User.objects.create_user(username="stream-boundary", is_staff=True)
    request = APIRequestFactory().get("/frame/")
    force_authenticate(request, user=user)
    sample = EncodedFrameSample(
        frame_number=7, timestamp=0.237, content_type="image/jpeg", image_bytes=b"jpeg"
    )
    with patch(
        "endoreg_db.views.media.frame_media.read_video_file_frame_jpeg",
        return_value=sample,
    ) as decode:
        response = view_class.as_view()(request, video_id=video.pk, frame_number=7)
    if busy:
        assert response.status_code == 409
        decode.assert_not_called()
        assert not (tmp_path / "absent").exists()
        return
    assert response.status_code == 200
    assert response.content == b"jpeg"
    assert response["X-Frame-Timestamp"] == "0.237"
    assert response["X-Frame-File-Type"] == "processed"
    assert decode.call_args.kwargs == {"frame_number": 7, "file_type": "processed"}
    frame.refresh_from_db()
    assert frame.timestamp == 0.237 and not frame.is_extracted
    assert not (tmp_path / "absent").exists()
    assert MediaOperationLease.objects.filter(video=video, lease_type="stream").exists()


@pytest.mark.parametrize("requested", [None, "auto", "processed"])
def test_annotation_selection_never_falls_back_to_raw(requested: str | None) -> None:
    from endoreg_db.views.video.ai.frame_annotations import (
        _parse_frame_file_type,
        _resolve_task_artifact_kind,
    )

    video = VideoFile(raw_file="sensitive_videos/raw.mp4")
    selection, error = _parse_frame_file_type(requested)
    assert error is None
    assert (
        _resolve_task_artifact_kind(video=video, requested_frame_file_type=selection)
        is None
    )


@pytest.mark.parametrize(
    "processed,requested,expected",
    [
        (True, None, "processed"),
        (False, None, "raw"),
        (True, "raw", "raw"),
        (True, "processed", "processed"),
    ],
)
def test_prediction_source_uses_annotation_generation_unless_explicit(
    processed: bool, requested: str | None, expected: str
) -> None:
    from endoreg_db.services.video_files._ai import _prediction_source

    video = VideoFile(
        raw_file="raw/source.mp4",
        processed_file="processed/master.mp4" if processed else "",
        meta=constant_rate_video_metadata(fps_num=25, frame_count=10),
    )
    with patch.object(
        video,
        "get_or_create_state",
        return_value=SimpleNamespace(frames_extracted=False),
    ):
        source = _prediction_source(
            video=video, frame_source_mode="stream", frame_source_file_type=requested
        )
    assert source.file_type == expected


@pytest.mark.parametrize("model_wrapper", [False, True])
@pytest.mark.parametrize("requested", [None, "raw"])
def test_prediction_wrappers_preserve_unspecified_or_explicit_artifact(
    model_wrapper: bool, requested: str | None
) -> None:
    from endoreg_db.models.metadata.model_meta import ModelMeta
    from endoreg_db.services.video_files.ai import predict_video

    video = VideoFile()
    with patch(
        "endoreg_db.services.video_files._ai._predict_video_pipeline", return_value={}
    ) as pipeline:
        if model_wrapper:
            if requested is None:
                video.predict_video(ModelMeta())
            else:
                video.predict_video(ModelMeta(), frame_source_file_type=requested)
        else:
            if requested is None:
                predict_video(video, ModelMeta())
            else:
                predict_video(video, ModelMeta(), frame_source_file_type=requested)
    assert pipeline.call_args.kwargs["frame_source_file_type"] == requested

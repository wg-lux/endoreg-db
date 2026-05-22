from __future__ import annotations

from pathlib import Path

import pytest
from django.core.files.base import ContentFile

from endoreg_db.config.env import DEFAULT_VIDEO_FPS
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.video.storage_mode import VideoStorageMode
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.video_files import (
    VideoArtifactKind,
    anonymize_video_file,
    delete_video_frame_range,
    extract_video_frame_range,
    extract_video_frames,
    get_active_video_file,
    get_or_create_video_state,
    get_video_by_content_hash,
    get_video_by_pk,
    get_video_fps,
    get_video_stream_relative_path,
    parse_video_artifact_kind,
    rebuild_processed_video_without_outside_frames,
    resolve_video_stream_source,
    run_video_pipe_1,
    run_video_pipe_2,
    video_hash_exists,
)


@pytest.fixture
def video_center() -> Center:
    return Center.objects.create(
        name="video-file-services",
        display_name="Video File Services",
    )


@pytest.fixture
def video(video_center: Center) -> VideoFile:
    return VideoFile.objects.create(
        center=video_center,
        video_hash="video-file-services-hash",
    )


@pytest.mark.django_db
def test_video_query_and_state_services_preserve_wrapper_behavior(video: VideoFile):
    assert video_hash_exists(video.video_hash) is True
    assert get_video_by_pk(video.pk) == video
    assert get_video_by_content_hash(video.video_hash) == video

    state = get_or_create_video_state(video)
    video.refresh_from_db()

    assert state.pk is not None
    assert video.state == state
    assert video.get_or_create_state() == state


@pytest.mark.django_db
def test_video_active_file_and_stream_services_preserve_wrapper_behavior(
    video: VideoFile,
    tmp_path: Path,
):
    video.raw_file.save("raw/service-active.mp4", ContentFile(b"raw"), save=True)
    video.processed_file.save(
        "processed/service-active.mp4",
        ContentFile(b"processed"),
        save=True,
    )

    assert get_active_video_file(video) == video.processed_file
    assert video.active_file == get_active_video_file(video)

    video.storage_mode = VideoStorageMode.STREAMABLE.value
    video.raw_streamable_relative_path = "streamable/raw/service-active.mp4"
    video.processed_streamable_relative_path = "streamable/processed/service-active.mp4"
    assert parse_video_artifact_kind("processed") == VideoArtifactKind.PROCESSED
    assert get_video_stream_relative_path(
        video, VideoArtifactKind.RAW
    ) == video.get_stream_relative_path("raw")
    assert get_video_stream_relative_path(
        video, VideoArtifactKind.PROCESSED
    ) == video.get_stream_relative_path("processed")

    stream_path = tmp_path / "streamable-processed.mp4"
    stream_path.write_bytes(b"processed")
    video.processed_file.name = "processed/service-active.mp4"
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            video,
            "get_processed_stream_path",
            lambda *, materialize_if_missing=False: stream_path,
        )
        assert resolve_video_stream_source(
            video,
            VideoArtifactKind.PROCESSED,
        ) == video.resolve_video_stream_source("processed")


@pytest.mark.django_db
def test_video_fps_service_preserves_wrapper_behavior(video: VideoFile):
    assert VideoFile.default_fps == DEFAULT_VIDEO_FPS
    assert get_video_fps(video) == video.get_fps()
    video.refresh_from_db()
    assert video.fps == DEFAULT_VIDEO_FPS


@pytest.mark.django_db
def test_video_frame_services_preserve_wrapper_behavior(video: VideoFile, monkeypatch):
    from endoreg_db.models.media.video import video_file_frames
    from endoreg_db.models.media.video.video_file_frames import _manage_frame_range

    extraction_calls = []
    range_calls = []
    deletion_calls = []

    def fake_extract(video_obj, *args, **kwargs):
        extraction_calls.append((video_obj, args, kwargs))
        return "full-extraction"

    def fake_extract_range(**kwargs):
        range_calls.append(kwargs)
        return True

    def fake_delete_range(**kwargs):
        deletion_calls.append(kwargs)

    monkeypatch.setattr(video_file_frames, "_extract_frames", fake_extract)
    monkeypatch.setattr(_manage_frame_range, "_extract_frame_range", fake_extract_range)
    monkeypatch.setattr(_manage_frame_range, "_delete_frame_range", fake_delete_range)

    assert extract_video_frames(video, overwrite=True) == "full-extraction"
    assert video.extract_frames(overwrite=True) == "full-extraction"
    assert len(extraction_calls) == 2

    assert (
        extract_video_frame_range(
            video,
            start_frame=2,
            end_frame=4,
            overwrite=True,
            quality=3,
            ext="png",
            verbose=True,
        )
        is True
    )
    assert video.extract_specific_frame_range(5, 7, overwrite=True) is True
    assert range_calls[0] == {
        "video": video,
        "start_frame": 2,
        "end_frame": 4,
        "quality": 3,
        "overwrite": True,
        "ext": "png",
        "verbose": True,
    }
    assert range_calls[1]["start_frame"] == 5
    assert range_calls[1]["end_frame"] == 7

    delete_video_frame_range(video, start_frame=8, end_frame=9)
    video.delete_specific_frame_range(10, 11)
    assert deletion_calls == [
        {"video": video, "start_frame": 8, "end_frame": 9},
        {"video": video, "start_frame": 10, "end_frame": 11},
    ]


@pytest.mark.django_db
def test_video_pipeline_and_anonymization_services_preserve_wrappers(
    video: VideoFile,
    monkeypatch,
):
    from endoreg_db.models.media.video import pipe_1, pipe_2, video_file_anonymize
    from endoreg_db.services import video_post_validation_blackening

    pipe_1_calls = []
    pipe_2_calls = []
    anonymize_calls = []
    rebuild_calls = []

    def fake_pipe_1(video_obj, *args, **kwargs):
        pipe_1_calls.append((video_obj, args, kwargs))
        return True

    def fake_pipe_2(video_obj):
        pipe_2_calls.append(video_obj)
        return True

    def fake_anonymize(video_obj, *, delete_original_raw=True):
        anonymize_calls.append((video_obj, delete_original_raw))
        return delete_original_raw

    def fake_rebuild(video_obj, *, only_validated=False, outside_intervals=None):
        rebuild_calls.append((video_obj, only_validated, outside_intervals))
        return True

    monkeypatch.setattr(pipe_1, "_pipe_1", fake_pipe_1)
    monkeypatch.setattr(pipe_2, "_pipe_2", fake_pipe_2)
    monkeypatch.setattr(video_file_anonymize, "_anonymize", fake_anonymize)
    monkeypatch.setattr(
        video_post_validation_blackening,
        "rebuild_processed_video_without_outside_frames",
        fake_rebuild,
    )

    assert run_video_pipe_1(video, ocr_frame_fraction=0.2) is True
    assert video.pipe_1(ocr_frame_fraction=0.2) is True
    assert pipe_1_calls == [
        (video, (), {"ocr_frame_fraction": 0.2}),
        (video, (), {"ocr_frame_fraction": 0.2}),
    ]

    assert run_video_pipe_2(video) is True
    assert video.pipe_2() is True
    assert pipe_2_calls == [video, video]

    assert anonymize_video_file(video, delete_original_raw=False) is False
    assert video.anonymize(delete_original_raw=True) is True
    assert anonymize_calls == [(video, False), (video, True)]

    intervals = [(1, 3)]
    assert (
        rebuild_processed_video_without_outside_frames(
            video,
            only_validated=True,
            outside_intervals=intervals,
        )
        is True
    )
    assert (
        VideoFile.create_video_without_outside_frames(
            video,
            only_validated=False,
            outside_intervals=intervals,
        )
        is True
    )
    assert rebuild_calls == [
        (video, True, intervals),
        (video, False, intervals),
    ]


def test_application_code_uses_video_file_services_for_high_risk_facade_methods():
    repo_root = Path(__file__).resolve().parents[2]
    scan_roots = [
        repo_root / "endoreg_db" / "export",
        repo_root / "endoreg_db" / "import_files",
        repo_root / "endoreg_db" / "serializers",
        repo_root / "endoreg_db" / "services",
        repo_root / "endoreg_db" / "utils" / "pipelines",
        repo_root / "endoreg_db" / "views",
    ]
    excluded_parts = {
        ("endoreg_db", "services", "video_files"),
    }
    disallowed_tokens = (
        ".pipe_1(",
        ".pipe_2(",
        ".anonymize(",
        ".predict_video(",
        ".extract_text_from_frames(",
        ".extract_frames(",
        ".extract_specific_frame_range(",
        ".delete_frames(",
        ".update_video_meta(",
        ".update_text_metadata(",
        ".get_fps(",
        ".ensure_local_raw_file(",
        ".ensure_local_processed_file(",
        # "video.validate_metadata_annotation(", #TODO Uncomment when fully externalized
        ".get_stream_relative_path(",
        ".resolve_video_stream_source(",
        ".can_offload_stream_with_nginx(",
        ".get_frame_dir_path(",
        ".get_outside_segments(",
        ".initialize_video_specs(",
        ".initialize_frames(",
        "VideoFile.create_from_file(",
        # "VideoFile.create_from_file_initialized(", #TODO Uncomment when fully externalized
        "VideoFile.get_video_by_pk(",
        "VideoFile.get_video_by_content_hash(",
        "VideoFile.create_video_without_outside_frames(",
    )

    offenders: list[str] = []
    for root in scan_roots:
        for path in root.rglob("*.py"):
            relative = path.relative_to(repo_root)
            parts = relative.parts
            if any(parts[: len(excluded)] == excluded for excluded in excluded_parts):
                continue
            for line_number, line in enumerate(path.read_text().splitlines(), start=1):
                if any(token in line for token in disallowed_tokens):
                    offenders.append(f"{relative}:{line_number}: {line.strip()}")

    assert offenders == []

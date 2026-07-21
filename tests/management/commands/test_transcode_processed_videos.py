from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any, Callable, cast

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.core.management import call_command
from pytest import MonkeyPatch

from endoreg_db.models import Center, VideoFile
from endoreg_db.services import video_processed_transcode as service
from endoreg_db.schemas.video_storage import VideoArtifactProbe, VideoTimelineContract
from endoreg_db.utils.encryption.encrypted import MAGIC
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.paths import EndoregPathsModel

pytestmark = pytest.mark.django_db


@pytest.fixture
def media_center() -> Center:
    return Center.objects.create(
        name="processed-transcode-center",
        display_name="Processed Transcode Center",
    )


def _create_processed_video(
    *,
    center: Center,
    payload: bytes = b"old processed video payload",
) -> VideoFile:
    video = VideoFile.objects.create(
        center=center,
        video_hash="raw-video-hash-for-processed-transcode",
        fps=25.0,
        duration=10.0,
        frame_count=250,
    )
    cast(Any, video.processed_file).save(
        "old-processed.mp4",
        ContentFile(payload),
        save=True,
    )
    video.processed_video_hash = sha256_file(video.processed_file)
    video.save(update_fields=["processed_video_hash", "date_modified"])
    return video


def _old_streamable_path(video: VideoFile) -> Path:
    paths = EndoregPathsModel.from_environment()
    streamable_path = (
        paths.storage
        / "streamable_videos"
        / "processed"
        / f"{video.video_hash}.old.mp4"
    )
    streamable_path.parent.mkdir(parents=True, exist_ok=True)
    streamable_path.write_bytes(b"\x00\x00\x00\x18ftypmp42old-streamable")
    video.processed_streamable_relative_path = streamable_path.relative_to(
        paths.storage
    ).as_posix()
    video.save(update_fields=["processed_streamable_relative_path", "date_modified"])
    return streamable_path


def _patch_transcode_and_streamable(
    monkeypatch: MonkeyPatch,
    *,
    output_payload: bytes = b"small mp4",
) -> None:
    def fake_transcode_video(
        input_path: Path,
        output_path: Path,
        **kwargs: object,
    ) -> Path:
        _ = input_path
        _ = kwargs
        output_path.write_bytes(output_payload)
        return output_path

    def fake_sync_video_streamable_artifacts(
        video: VideoFile,
        *,
        include_raw: bool = True,
        include_processed: bool = True,
        save: bool = True,
    ) -> list[str]:
        _ = include_raw
        if not include_processed:
            return []
        processed_video_hash = cast(str, video.processed_video_hash)
        paths = EndoregPathsModel.from_environment()
        streamable_path = (
            paths.storage
            / "streamable_videos"
            / "processed"
            / f"{processed_video_hash}.mp4"
        )
        streamable_path.parent.mkdir(parents=True, exist_ok=True)
        streamable_path.write_bytes(b"\x00\x00\x00\x18ftypmp42new-streamable")
        video.processed_streamable_relative_path = streamable_path.relative_to(
            paths.storage
        ).as_posix()
        if save:
            video.save(
                update_fields=["processed_streamable_relative_path", "date_modified"]
            )
        return ["processed_streamable_relative_path"]

    monkeypatch.setattr(service, "transcode_video", fake_transcode_video)
    probe = VideoArtifactProbe(
        codec_name="h264",
        pixel_format="yuv420p",
        width=1920,
        height=1080,
        bit_rate_bps=800_000,
        size_bytes=max(1, len(output_payload)),
        timeline=VideoTimelineContract(
            fps_num=25,
            fps_den=1,
            duration_seconds=10.0,
            frame_count=250,
        ),
    )

    def fake_probe_video_artifact(_path: Path) -> VideoArtifactProbe:
        return probe

    def fake_materialize_video_hls(*args: object, **kwargs: object) -> None:
        _ = args
        _ = kwargs

    monkeypatch.setattr(service, "probe_video_artifact", fake_probe_video_artifact)
    monkeypatch.setattr(
        service,
        "materialize_video_hls",
        fake_materialize_video_hls,
    )
    monkeypatch.setattr(
        service,
        "sync_video_streamable_artifacts",
        fake_sync_video_streamable_artifacts,
    )


def test_transcode_processed_videos_apply_updates_hash_reencrypts_and_cleans_old_assets(
    media_center: Center,
    monkeypatch: MonkeyPatch,
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    video = _create_processed_video(center=media_center)
    old_processed_name = video.processed_file.name
    old_processed_path = Path(video.processed_file.path)
    old_streamable_path = _old_streamable_path(video)
    old_hash = cast(str, video.processed_video_hash)
    _patch_transcode_and_streamable(monkeypatch)

    stdout = StringIO()
    with django_capture_on_commit_callbacks(execute=True):
        call_command(
            "transcode_processed_videos",
            "--video-id",
            str(video.pk),
            "--apply",
            "--json",
            stdout=stdout,
        )

    payload = json.loads(stdout.getvalue())
    assert payload["summary"]["changed"] == 1
    assert not old_processed_path.exists()
    assert not old_streamable_path.exists()

    video.refresh_from_db()
    assert video.processed_video_hash != old_hash
    assert video.processed_file.name != old_processed_name
    processed_file = video.processed_file
    processed_name = processed_file.name
    processed_storage = cast(Storage, getattr(processed_file, "storage"))
    assert processed_name is not None
    assert processed_storage.exists(processed_name)
    processed_video_hash = cast(str, video.processed_video_hash)
    assert video.processed_streamable_relative_path.endswith(
        f"{processed_video_hash}.mp4"
    )
    with Path(video.processed_file.path).open("rb") as stored:
        assert stored.read(len(MAGIC)) == MAGIC
    with video.processed_file.open("rb") as decrypted:
        assert decrypted.read() == b"small mp4"


def test_transcode_processed_videos_dry_run_does_not_replace_or_cleanup(
    media_center: Center,
    monkeypatch: MonkeyPatch,
) -> None:
    video = _create_processed_video(center=media_center)
    old_processed_name = video.processed_file.name
    old_processed_path = Path(video.processed_file.path)
    old_streamable_path = _old_streamable_path(video)
    old_hash = cast(str, video.processed_video_hash)
    _patch_transcode_and_streamable(monkeypatch)

    stdout = StringIO()
    call_command(
        "transcode_processed_videos",
        "--video-id",
        str(video.pk),
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["summary"]["dry_run"] == 1
    assert old_processed_path.exists()
    assert old_streamable_path.exists()

    video.refresh_from_db()
    assert video.processed_video_hash == old_hash
    assert video.processed_file.name == old_processed_name


def test_transcode_processed_videos_skips_output_that_is_not_smaller(
    media_center: Center,
    monkeypatch: MonkeyPatch,
) -> None:
    video = _create_processed_video(center=media_center, payload=b"old")
    old_hash = cast(str, video.processed_video_hash)
    _patch_transcode_and_streamable(monkeypatch, output_payload=b"larger-output")

    stdout = StringIO()
    call_command(
        "transcode_processed_videos",
        "--video-id",
        str(video.pk),
        "--apply",
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["summary"]["skipped"] == 1
    assert payload["results"][0]["status"] == "skipped_not_smaller"
    video.refresh_from_db()
    assert video.processed_video_hash == old_hash

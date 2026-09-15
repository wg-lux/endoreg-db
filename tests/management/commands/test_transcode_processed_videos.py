from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from typing import Any, Callable, cast

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.core.management import call_command
from django.db.models.fields.files import FieldFile
from pytest import MonkeyPatch

from endoreg_db.models import Center, VideoFile
from endoreg_db.services import (
    video_processed_transcode as service,
    processed_video_cleanup as cleanup,
    hls_media,
)
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.utils.paths import to_protected_media_relative
from endoreg_db.services.hls_media import HlsMaterializationResult
from endoreg_db.schemas.video_storage import VideoArtifactProbe, VideoTimelineContract
from endoreg_db.utils.encryption.encrypted import MAGIC
from endoreg_db.utils.file_operations import sha256_file

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
        f"{video.video_hash}.mp4",
        ContentFile(payload),
        save=True,
    )
    video.processed_video_hash = sha256_file(video.processed_file)
    video.save(update_fields=["processed_video_hash", "date_modified"])
    return video


def _old_streamable_path(video: VideoFile) -> Path:
    streamable_path = (
        hls_media.streamable_media.STREAMABLE_PROCESSED_VIDEO_ROOT
        / f"{video.video_hash}.old.mp4"
    )
    streamable_path.parent.mkdir(parents=True, exist_ok=True)
    streamable_path.write_bytes(b"\x00\x00\x00\x18ftypmp42old-streamable")
    video.processed_streamable_relative_path = to_protected_media_relative(
        streamable_path
    )
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

    def fake_materialize_video_hls(
        video_id: int, **kwargs: object
    ) -> HlsMaterializationResult:
        _ = kwargs
        current = VideoFile.objects.get(pk=video_id)
        VideoHlsArtifact.objects.update_or_create(
            video_id=video_id,
            artifact_kind="processed",
            status="ready",
            defaults={
                "source_file_name": str(current.processed_file.name),
                "source_content_hash": current.processed_video_hash,
            },
        )
        return HlsMaterializationResult(
            video_id=video_id,
            artifact_kind="processed",
            status="materialized",
            key_id="test",
            playlist_relative_path="test/index.m3u8",
            segment_directory_relative_path="test",
            segment_count=1,
        )

    def ready(*, video: VideoFile, artifact_kind: str) -> VideoHlsArtifact:
        return VideoHlsArtifact.objects.get(
            video=video, artifact_kind=artifact_kind, status="ready"
        )

    monkeypatch.setattr(hls_media, "get_ready_hls_artifact", ready)
    monkeypatch.setattr(service, "probe_video_artifact", fake_probe_video_artifact)
    monkeypatch.setattr(
        service,
        "materialize_video_hls",
        fake_materialize_video_hls,
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
    assert video.processed_streamable_relative_path == ""
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


@pytest.mark.django_db(transaction=True)
def test_post_commit_cleanup_failure_preserves_published_encrypted_master(
    media_center: Center,
    monkeypatch: MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    video = _create_processed_video(center=media_center)
    old_name = video.processed_file.name
    _patch_transcode_and_streamable(monkeypatch)

    def reject_deletion(_field_file: FieldFile, **_kwargs: object) -> bool:
        raise OSError("cleanup unavailable")

    monkeypatch.setattr(cleanup, "safe_delete_field_file", reject_deletion)
    with pytest.raises(service.ProcessedVideoTranscodeCleanupError) as raised:
        service.transcode_processed_video_for_storage_pressure(video, apply=True)

    assert raised.value.phase == "published_generation"
    video.refresh_from_db()
    assert video.processed_file.name != old_name
    with video.processed_file.open("rb") as decrypted:
        assert decrypted.read() == b"small mp4"
    with Path(video.processed_file.path).open("rb") as encrypted:
        assert encrypted.read(len(MAGIC)) == MAGIC
    assert "processed_video_transcode_cleanup_failed" in caplog.text
    assert "requires_reconciliation" in caplog.text


def test_source_cleanup_failure_prevents_publication(
    media_center: Center,
    monkeypatch: MonkeyPatch,
) -> None:
    video = _create_processed_video(center=media_center)
    _patch_transcode_and_streamable(monkeypatch)
    materialize = service.ensure_local_processed_video_file

    @contextmanager
    def fail_source_cleanup(
        current_video: VideoFile, *, directory: Path | None = None
    ) -> Generator[Path]:
        with materialize(current_video, directory=directory) as source:
            yield source
        raise OSError("source cleanup failed before publication")

    monkeypatch.setattr(
        service, "ensure_local_processed_video_file", fail_source_cleanup
    )

    result = service.transcode_processed_video_for_storage_pressure(video, apply=True)
    assert result.status == "failed"
    assert not result.published
    video.refresh_from_db()
    with video.processed_file.open("rb") as decrypted:
        assert decrypted.read() == b"old processed video payload"


def test_prepublication_cleanup_failure_is_loud_and_preserves_original(
    media_center: Center,
    monkeypatch: MonkeyPatch,
) -> None:
    video = _create_processed_video(center=media_center)
    old_name = video.processed_file.name
    _patch_transcode_and_streamable(monkeypatch)

    def reject_streamable(*_args: object, **_kwargs: object) -> list[str]:
        raise RuntimeError("streamable publication failed")

    def reject_deletion(_field_file: FieldFile, **_kwargs: object) -> bool:
        raise OSError("candidate cleanup unavailable")

    monkeypatch.setattr(service, "_publish_transcode_candidate", reject_streamable)
    monkeypatch.setattr(service, "safe_delete_field_file", reject_deletion)
    with pytest.raises(service.ProcessedVideoTranscodeCleanupError) as raised:
        service.transcode_processed_video_for_storage_pressure(video, apply=True)

    assert raised.value.phase == "failed_candidate"
    video.refresh_from_db()
    assert video.processed_file.name == old_name
    with video.processed_file.open("rb") as decrypted:
        assert decrypted.read() == b"old processed video payload"

    retained = set(Path(video.processed_file.path).parent.rglob("*.mp4"))
    with pytest.raises(RuntimeError, match="staging requires cleanup"):
        service.transcode_processed_video_for_storage_pressure(video, apply=True)
    assert set(Path(video.processed_file.path).parent.rglob("*.mp4")) == retained

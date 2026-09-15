from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import uuid4
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.db import connection

from endoreg_db.models import Center, VideoFile
from endoreg_db.schemas.video_storage import VideoArtifactProbe, VideoTimelineContract
from endoreg_db.services import (
    video_processed_transcode as service,
    hls_media,
    processed_video_cleanup as cleanup,
)
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.schemas.processed_video_cleanup import (
    cleanup_receipts,
)
from endoreg_db.services.hls_media import HlsMaterializationResult
from endoreg_db.utils.encryption.encrypted import EncryptedStorage, MAGIC
from endoreg_db.utils.file_operations import atomic_write_file, sha256_file
from endoreg_db.utils.paths import EndoregPathsModel
from endoreg_db.utils.storage.video_fields import VideoArtifactFieldFile

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def video() -> VideoFile:
    center = Center.objects.create(
        name="encrypted-transcode", display_name="Encrypted transcode"
    )
    current = VideoFile.objects.create(
        center=center,
        video_hash=uuid4().hex,
        fps=25.0,
        duration=10.0,
        frame_count=250,
    )
    assert isinstance(current.processed_file, VideoArtifactFieldFile)
    current.processed_file.save(
        f"{current.video_hash}.mp4",
        ContentFile(b"original processed payload" * 30),
        save=True,
    )
    current.processed_video_hash = sha256_file(current.processed_file)
    current.save(update_fields=["processed_video_hash"])
    return current


def probe() -> VideoArtifactProbe:
    return VideoArtifactProbe(
        codec_name="h264",
        pixel_format="yuv420p",
        width=1920,
        height=1080,
        bit_rate_bps=800_000,
        size_bytes=10,
        timeline=VideoTimelineContract(
            fps_num=25, fps_den=1, duration_seconds=10, frame_count=250
        ),
    )


def ready_hls(video_id: int, **_kwargs: object) -> HlsMaterializationResult:
    assert not connection.in_atomic_block, (
        "HLS must run outside publication transaction"
    )
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


@pytest.fixture(autouse=True)
def validated_hls(monkeypatch: pytest.MonkeyPatch) -> None:
    # The encoder/HLS integration suite verifies segment encryption and playlists.
    # This suite exercises real encrypted storage and publication cleanup.
    def ready(*, video: VideoFile, artifact_kind: str) -> VideoHlsArtifact:
        return VideoHlsArtifact.objects.get(
            video=video, artifact_kind=artifact_kind, status="ready"
        )

    monkeypatch.setattr(hls_media, "get_ready_hls_artifact", ready)


def test_ciphertext_source_scoped_plaintext_and_encrypted_publication(
    video: VideoFile,
) -> None:
    original_name = str(video.processed_file.name)
    original_path = Path(video.processed_file.path)
    assert original_path.read_bytes().startswith(MAGIC)
    scoped_paths: list[Path] = []

    def encode(source: Path, destination: Path, **kwargs: object) -> Path:
        assert not connection.in_atomic_block
        assert source.read_bytes() == b"original processed payload" * 30
        assert source.stat().st_mode & 0o777 == 0o600
        assert source.parent.stat().st_mode & 0o777 == 0o700
        assert destination.stat().st_mode & 0o777 == 0o600
        assert source.parent == destination.parent
        assert source.is_relative_to(EndoregPathsModel.from_environment().transcoding)
        scoped_paths.extend([source, destination])
        args = kwargs["extra_args"]
        assert isinstance(args, list)
        assert "-r" not in args
        assert "passthrough" in args
        atomic_write_file(
            destination=destination, content=[b"small copy"], file_mode=0o600
        )
        return destination

    with (
        patch.object(service, "transcode_video", side_effect=encode),
        patch.object(service, "probe_video_artifact", return_value=probe()),
        patch.object(service, "materialize_video_hls", side_effect=ready_hls),
    ):
        result = service.transcode_processed_video_for_storage_pressure(
            video, apply=True
        )
    assert result.status == "changed"
    assert result.published
    video.refresh_from_db()
    target = video.processed_file
    assert Path(target.path).read_bytes().startswith(MAGIC)
    with target.open("rb") as plaintext:
        assert plaintext.read() == b"small copy"
    assert video.processed_file.name != original_name
    assert not original_path.exists()
    assert scoped_paths
    assert all(not path.exists() for path in scoped_paths)
    assert not scoped_paths[0].parent.exists()


@pytest.mark.parametrize("failure", ["wrong_key", "tamper", "loader"])
def test_crypto_and_loader_failures_preserve_source_and_cleanup(
    video: VideoFile, failure: str
) -> None:
    original_name = str(video.processed_file.name)
    paths: list[Path] = []
    # Keep the actual database model and real encrypted storage; change only key
    # resolution for the reader, or one authenticated ciphertext byte.
    current_storage = cast(EncryptedStorage, getattr(video.processed_file, "storage"))
    if failure == "wrong_key":
        wrong_storage = EncryptedStorage(
            location=cast(str, getattr(current_storage, "location")),
            master_key=b"x" * 32,
        )
        storage_patch = patch.object(
            video.processed_file.field, "storage", wrong_storage
        )
    else:
        storage_patch = patch.object(
            video.processed_file.field, "storage", current_storage
        )
    if failure == "tamper":
        ciphertext_path = Path(video.processed_file.path)
        ciphertext = bytearray(ciphertext_path.read_bytes())
        ciphertext[-1] ^= 1
        atomic_write_file(destination=ciphertext_path, content=[bytes(ciphertext)])

    def fail_probe(path: Path) -> VideoArtifactProbe:
        paths.append(path)
        raise RuntimeError("decoder failed")

    with (
        storage_patch,
        patch.object(service, "probe_video_artifact", side_effect=fail_probe) as load,
        patch.object(service, "transcode_video") as encode,
    ):
        result = service.transcode_processed_video_for_storage_pressure(
            video, apply=True
        )
    assert result.status == "failed"
    assert not result.published
    video.refresh_from_db()
    assert video.processed_file.name == original_name
    assert current_storage.exists(original_name)
    encode.assert_not_called()
    if failure != "loader":
        load.assert_not_called()
    assert all(not path.exists() for path in paths)
    staging = (
        EndoregPathsModel.from_environment().transcoding
        / "processed_storage_pressure"
        / str(video.uuid)
    )
    assert not staging.exists()


def test_postpublication_hls_failure_preserves_old_and_published_files(
    video: VideoFile,
) -> None:
    original_path = Path(video.processed_file.path)

    def encode(_source: Path, destination: Path, **_kwargs: object) -> Path:
        atomic_write_file(
            destination=destination, content=[b"small copy"], file_mode=0o600
        )
        return destination

    with (
        patch.object(service, "transcode_video", side_effect=encode),
        patch.object(service, "probe_video_artifact", return_value=probe()),
        patch.object(
            service, "materialize_video_hls", side_effect=RuntimeError("HLS failed")
        ),
    ):
        result = service.transcode_processed_video_for_storage_pressure(
            video, apply=True
        )
    assert result.status == "failed"
    assert result.published
    assert result.failure_stage == "rebuilding_playback"
    assert original_path.exists()
    video.refresh_from_db()
    target = video.processed_file
    assert Path(target.path).exists()
    assert Path(target.path).read_bytes().startswith(MAGIC)


def test_plaintext_cleanup_failure_is_loud(video: VideoFile) -> None:
    with (
        patch(
            "endoreg_db.utils.encryption.storage_materialization.safe_unlink_file",
            side_effect=OSError("cleanup denied"),
        ),
        patch.object(
            service, "probe_video_artifact", side_effect=RuntimeError("decoder failed")
        ),
    ):
        result = service.transcode_processed_video_for_storage_pressure(
            video, apply=True
        )
    assert result.status == "failed"
    assert not result.published


def test_repeated_replacements_keep_one_encrypted_master_and_preserve_raw(
    video: VideoFile,
) -> None:
    assert isinstance(video.raw_file, VideoArtifactFieldFile)
    video.raw_file.save("retained-raw.mp4", ContentFile(b"raw payload"), save=True)
    raw_name = str(video.raw_file.name)
    raw_path = Path(video.raw_file.path)
    original_path = Path(video.processed_file.path)
    payloads = iter([b"a" * 100, b"b" * 60, b"c" * 20])
    retired: list[Path] = [original_path]

    def encode(_source: Path, destination: Path, **_kwargs: object) -> Path:
        atomic_write_file(
            destination=destination, content=[next(payloads)], file_mode=0o600
        )
        return destination

    with (
        patch.object(service, "transcode_video", side_effect=encode),
        patch.object(service, "probe_video_artifact", return_value=probe()),
        patch.object(service, "materialize_video_hls", side_effect=ready_hls),
    ):
        for _ in range(3):
            result = service.transcode_processed_video_for_storage_pressure(
                video, apply=True
            )
            assert result.status == "changed"
            video.refresh_from_db()
            assert not cleanup_receipts(video.meta)
            assert all(not path.exists() for path in retired)
            current_path = Path(video.processed_file.path)
            assert list(current_path.parent.glob(f"{video.video_hash}-*.mp4")) == [
                current_path
            ]
            assert current_path.read_bytes().startswith(MAGIC)
            assert str(video.raw_file.name) == raw_name
            assert raw_path.exists()
            retired.append(current_path)
    assert not (
        EndoregPathsModel.from_environment().transcoding
        / "processed_storage_pressure"
        / str(video.uuid)
    ).exists()


def test_cleanup_receipt_blocks_further_encoding_and_retries_real_deletion(
    video: VideoFile,
) -> None:
    old_path = Path(video.processed_file.path)
    payloads = iter([b"a" * 100, b"b" * 60])

    def encode(_source: Path, destination: Path, **_kwargs: object) -> Path:
        atomic_write_file(
            destination=destination, content=[next(payloads)], file_mode=0o600
        )
        return destination

    with (
        patch.object(service, "transcode_video", side_effect=encode) as encoder,
        patch.object(service, "probe_video_artifact", return_value=probe()),
        patch.object(service, "materialize_video_hls", side_effect=ready_hls),
    ):
        with patch.object(
            cleanup,
            "safe_delete_field_file",
            side_effect=OSError("storage unavailable"),
        ):
            for _ in range(2):
                with pytest.raises(service.ProcessedVideoTranscodeCleanupError):
                    service.transcode_processed_video_for_storage_pressure(
                        video, apply=True
                    )
                video.refresh_from_db()
                assert len(cleanup_receipts(video.meta)) == 1
                assert old_path.exists()
            assert encoder.call_count == 1
        first_replacement = Path(video.processed_file.path)
        result = service.transcode_processed_video_for_storage_pressure(
            video, apply=True
        )
        assert result.status == "changed"
        assert encoder.call_count == 2
    video.refresh_from_db()
    assert not cleanup_receipts(video.meta)
    assert not old_path.exists()
    assert not first_replacement.exists()
    assert Path(video.processed_file.path).exists()


def test_failed_attempt_cleanup_blocks_another_attempt_directory(
    video: VideoFile,
) -> None:
    with patch.object(service, "safe_rmtree", side_effect=OSError("cleanup denied")):
        with pytest.raises(service.ProcessedVideoTranscodeCleanupError):
            service.transcode_processed_video_for_storage_pressure(video, apply=False)
    root = (
        EndoregPathsModel.from_environment().transcoding
        / "processed_storage_pressure"
        / str(video.uuid)
    )
    existing = set(root.rglob("*"))
    with patch.object(service, "transcode_video") as encoder:
        with pytest.raises(RuntimeError, match="staging requires cleanup"):
            service.transcode_processed_video_for_storage_pressure(video, apply=True)
    encoder.assert_not_called()
    assert set(root.rglob("*")) == existing

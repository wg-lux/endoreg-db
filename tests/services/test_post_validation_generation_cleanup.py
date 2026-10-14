from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from django.core.files.base import ContentFile
from django.db import connection

from endoreg_db.models import Center, VideoFile
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.schemas.processed_video_cleanup import cleanup_receipts
from endoreg_db.services.streaming import hls_media
from endoreg_db.services.video_files import post_validation_blackening as service
from endoreg_db.services.video_storage import generation_cleanup as cleanup
from endoreg_db.utils.encryption.encrypted import MAGIC, EncryptedStorage
from endoreg_db.utils.file_operations import atomic_write_file, get_file_hash
from endoreg_db.utils.paths import get_runtime_paths
from tests.services.test_video_processed_transcode_encryption import probe, ready_hls
from endoreg_db.services.video_storage import canonical_timelines
from tests.helpers.canonical_timestamps import decoded_test_timestamps

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "bitrates, succeeds",
    [
        ([16_012_805, 800_000], True),
        ([16_012_805, 14_000_000, 800_000], True),
        ([16_012_805, 14_000_000, 13_000_000], False),
    ],
)
def test_bitrate_retry_preserves_source_until_validated(
    video: VideoFile,
    monkeypatch: pytest.MonkeyPatch,
    bitrates: list[int],
    succeeds: bool,
) -> None:
    original_name = str(video.processed_file.name)
    caps: list[object] = []
    paths: list[Path] = []
    monkeypatch.setattr(
        service,
        "probe_video_artifact",
        Mock(
            side_effect=[
                probe(),
                *(
                    probe().model_copy(update={"bit_rate_bps": rate})
                    for rate in bitrates
                ),
            ]
        ),
    )

    def encode(source: Path, output: Path, **kwargs: object) -> Path:
        # Every attempt consumes the same decrypted original, not its predecessor.
        assert source.read_bytes() == b"processed"
        assert video.processed_file.name == original_name
        assert video.processed_file.storage.exists(original_name)
        assert kwargs["intervals"] == [(10, 20)]
        caps.append(kwargs.get("max_bit_rate_bps"))
        paths.extend([source, output])
        atomic_write_file(destination=output, content=[b"blackened"], file_mode=0o600)
        return output

    monkeypatch.setattr(service, "blacken_video_frame_intervals", encode)
    assert (
        service.rebuild_processed_video_without_outside_frames(
            video, outside_intervals=[(10, 20)]
        )
        is succeeds
    )
    assert len(caps) == len(bitrates)
    assert caps[0] is None
    previous = 12_000_000
    for cap in caps[1:]:
        assert isinstance(cap, int) and 0 < cap < previous
        previous = cap
    video.refresh_from_db()
    assert Path(video.processed_file.path).read_bytes().startswith(MAGIC)
    if not succeeds:
        assert video.processed_file.name == original_name
        assert video.processed_file.storage.exists(original_name)
    assert all(not path.exists() for path in paths)


def test_blackening_does_not_retry_timeline_failure(
    video: VideoFile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_name = video.processed_file.name
    invalid = probe().model_copy(
        update={"timeline": probe().timeline.model_copy(update={"frame_count": 249})}
    )
    monkeypatch.setattr(
        service, "probe_video_artifact", Mock(side_effect=[probe(), invalid])
    )

    def encode(source: Path, output: Path, **kwargs: object) -> Path:
        return output

    encoder = Mock(side_effect=encode)
    monkeypatch.setattr(service, "blacken_video_frame_intervals", encoder)
    assert not service.rebuild_processed_video_without_outside_frames(
        video, outside_intervals=[(10, 20)]
    )
    assert encoder.call_count == 1
    video.refresh_from_db()
    assert video.processed_file.name == original_name


@pytest.fixture
def video(monkeypatch: pytest.MonkeyPatch) -> VideoFile:
    monkeypatch.setattr(
        canonical_timelines, "probe_video_frame_timestamps", decoded_test_timestamps
    )
    center = Center.objects.create(name=f"blackening-{uuid4().hex}")
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash=uuid4().hex,
        fps=25,
        duration=10,
        frame_count=250,
    )
    video.raw_file.save(f"{video.raw_video_hash}.mp4", ContentFile(b"raw"))
    video.processed_file.save(
        f"{video.raw_video_hash}_filtered.mp4", ContentFile(b"processed")
    )
    video.processed_video_hash = get_file_hash(video.processed_file)
    video.save()
    monkeypatch.setattr(service, "probe_video_artifact", Mock(return_value=probe()))
    monkeypatch.setattr(service, "materialize_video_hls", ready_hls)

    def ready(*, video: VideoFile, artifact_kind: str) -> VideoHlsArtifact:
        return VideoHlsArtifact.objects.get(
            video=video, artifact_kind=artifact_kind, status="ready"
        )

    monkeypatch.setattr(hls_media, "get_ready_hls_artifact", ready)
    return video


def test_repeated_rebuilds_keep_one_encrypted_master_and_no_plaintext(
    video: VideoFile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scoped_paths: list[Path] = []
    raw_name = str(video.raw_file.name)

    def encode(source: Path, output: Path, **kwargs: object) -> Path:
        assert not connection.in_atomic_block
        assert not source.read_bytes().startswith(MAGIC)
        assert source.stat().st_mode & 0o777 == 0o600
        assert output.stat().st_mode & 0o777 == 0o600
        scoped_paths.extend([source, output])
        atomic_write_file(destination=output, content=[uuid4().bytes], file_mode=0o600)
        return output

    monkeypatch.setattr(service, "blacken_video_frame_intervals", encode)
    for attempt in range(3):
        previous_name = str(video.processed_file.name)
        assert service.rebuild_processed_video_without_outside_frames(
            video, outside_intervals=[(10, 20)]
        )
        video.refresh_from_db()
        assert not video.processed_file.storage.exists(previous_name)
        assert Path(video.processed_file.path).read_bytes().startswith(MAGIC)
        assert not cleanup_receipts(video.meta)
        assert video.raw_file.name == raw_name
        history = VideoFileMetaPayload.model_validate(
            video.meta
        ).canonical_timeline_history
        assert history is not None and len(history) == attempt + 1
        assert all(not path.exists() for path in scoped_paths)
    assert (
        len(list(get_runtime_paths().anonym_video.glob(f"{video.raw_video_hash}*.mp4")))
        == 1
    )


@pytest.mark.parametrize("failure", ["wrong_key", "tamper", "loader", "encoder"])
@pytest.mark.parametrize("raise_on_error", [False, True])
def test_failures_preserve_encrypted_master_and_remove_scoped_plaintext(
    video: VideoFile,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    raise_on_error: bool,
) -> None:
    original_name = str(video.processed_file.name)
    paths: list[Path] = []
    storage = video.processed_file.storage
    wrong = EncryptedStorage(
        location=str(getattr(storage, "location")), master_key=b"x" * 32
    )
    if failure == "tamper":
        path = Path(video.processed_file.path)
        content = bytearray(path.read_bytes())
        content[-1] ^= 1
        atomic_write_file(destination=path, content=[bytes(content)])

    def load(path: Path):
        paths.append(path)
        if failure == "loader":
            raise RuntimeError("decoder failed")
        return probe()

    def encode(source: Path, output: Path, **kwargs: object) -> Path:
        paths.extend([source, output])
        raise RuntimeError("encoder failed")

    monkeypatch.setattr(service, "probe_video_artifact", load)
    encoder = Mock(side_effect=encode)
    monkeypatch.setattr(service, "blacken_video_frame_intervals", encoder)
    with patch.object(
        video.processed_file.field,
        "storage",
        wrong if failure == "wrong_key" else storage,
    ):
        if raise_on_error:
            with pytest.raises(RuntimeError, match="Outside-frame rebuild failed"):
                service.rebuild_processed_video_without_outside_frames(
                    video, outside_intervals=[(10, 20)], raise_on_error=True
                )
        else:
            assert not service.rebuild_processed_video_without_outside_frames(
                video, outside_intervals=[(10, 20)]
            )
    video.refresh_from_db()
    assert video.processed_file.name == original_name
    assert storage.exists(original_name)
    assert all(not path.exists() for path in paths)
    if failure != "encoder":
        encoder.assert_not_called()
    assert not list(
        get_runtime_paths().transcoding.glob(
            f"{video.raw_video_hash}.outside_frame_blackening.*"
        )
    )


@pytest.mark.parametrize("failure", ["hls", "cleanup"])
def test_postpublication_failure_blocks_accumulation_and_keeps_receipt(
    video: VideoFile,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    previous_name = str(video.processed_file.name)

    def encode(source: Path, output: Path, **kwargs: object) -> Path:
        atomic_write_file(destination=output, content=[b"blackened"], file_mode=0o600)
        return output

    monkeypatch.setattr(service, "blacken_video_frame_intervals", encode)
    if failure == "hls":
        monkeypatch.setattr(
            service,
            "materialize_video_hls",
            Mock(side_effect=RuntimeError("HLS unavailable")),
        )
    else:
        monkeypatch.setattr(
            cleanup,
            "safe_delete_field_file",
            Mock(side_effect=OSError("delete failed")),
        )
    assert service.rebuild_processed_video_without_outside_frames(
        video, outside_intervals=[(10, 20)]
    ) is (failure == "cleanup")
    video.refresh_from_db()
    assert video.processed_file.storage.exists(previous_name)
    assert video.processed_file.exists()
    assert cleanup_receipts(video.meta)[0].committed is (failure == "cleanup")
    encoder = Mock()
    monkeypatch.setattr(service, "blacken_video_frame_intervals", encoder)
    assert not service.rebuild_processed_video_without_outside_frames(
        video, outside_intervals=[(10, 20)]
    )
    encoder.assert_not_called()

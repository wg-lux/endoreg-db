from __future__ import annotations

from pathlib import Path
from typing import cast
from django.core.files.storage import Storage
from hashlib import sha256
from uuid import uuid4
from unittest.mock import Mock

import pytest
from django.core.files.base import ContentFile
from django.db import connection
from lx_dtypes.models.contracts.endoscopy_processor import RoiBoxCore

from endoreg_db.models import Center, VideoFile, SensitiveMeta
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.schemas.processed_video_cleanup import cleanup_receipts
from endoreg_db.services import hls_media, processed_video_cleanup as cleanup
from endoreg_db.services.video_files import _anonymization as service
from endoreg_db.utils.file_operations import atomic_write_file, sha256_file
from endoreg_db.utils.encryption.encrypted import MAGIC
from endoreg_db.utils.storage.video_fields import VideoArtifactFieldFile
from tests.services.test_video_processed_transcode_encryption import probe

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def video(monkeypatch: pytest.MonkeyPatch) -> VideoFile:
    center = Center.objects.create(name=f"reanonymize-{uuid4().hex}")
    sensitive = SensitiveMeta.objects.create(center=center)
    state = sensitive.get_or_create_state()
    state.dob_verified = True
    state.names_verified = True
    state.save()
    video = VideoFile.objects.create(
        center=center,
        sensitive_meta=sensitive,
        video_hash=uuid4().hex,
        fps=25,
        duration=10,
        frame_count=250,
    )
    raw = video.raw_file
    assert isinstance(raw, VideoArtifactFieldFile)
    raw.save(f"{video.video_hash}.mp4", ContentFile(b"raw input"))
    # The existing canonical master has the name emitted by processed transcoding.
    payload = b"transcoded processed video"
    old_name = f"{sha256(payload).hexdigest()}.{uuid4().hex}.mp4"
    processed = video.processed_file
    assert isinstance(processed, VideoArtifactFieldFile)
    processed.save(old_name, ContentFile(payload))
    video.processed_video_hash = sha256_file(video.processed_file)
    video.save()
    video.get_or_create_state()

    def roi(self: VideoFile) -> dict[str, int]:
        return {"x": 0, "y": 0, "width": 32, "height": 32}

    def fake_probe(path: Path):
        return probe()

    monkeypatch.setattr(VideoFile, "get_endo_roi", roi)
    monkeypatch.setattr(service, "probe_video_artifact", fake_probe)

    def mask(
        input_path: Path, output_path: Path, *, endo_roi: RoiBoxCore, intervals: object
    ) -> Path:
        assert not connection.in_atomic_block
        atomic_write_file(
            destination=output_path, content=[f"masked-{uuid4().hex}".encode()]
        )
        return output_path

    def ready(video: VideoFile, **kwargs: object) -> None:
        assert not connection.in_atomic_block
        VideoHlsArtifact.objects.filter(video=video, status="ready").delete()
        VideoHlsArtifact.objects.create(
            video=video,
            artifact_kind="processed",
            status="ready",
            source_file_name=str(video.processed_file.name),
            source_content_hash=video.processed_video_hash,
        )

    def get_ready(*, video: VideoFile, artifact_kind: str) -> VideoHlsArtifact:
        return VideoHlsArtifact.objects.get(
            video=video, artifact_kind=artifact_kind, status="ready"
        )

    monkeypatch.setattr(service, "mask_video_to_roi_and_blacken_intervals", mask)
    monkeypatch.setattr(service, "ensure_video_hls", ready)
    monkeypatch.setattr(hls_media, "get_ready_hls_artifact", get_ready)
    return video


def test_repeated_reanonymization_retires_transcoded_and_previous_generations(
    video: VideoFile,
) -> None:
    raw_name = str(video.raw_file.name)
    for attempt in range(3):
        previous_name = str(video.processed_file.name)
        video.meta = {**(video.meta or {}), "clinical_review": {"attempt": attempt}}
        assert video.anonymize(delete_original_raw=False)
        video.refresh_from_db()
        assert video.processed_file.name != previous_name
        assert "/.generations/" in str(video.processed_file.name)
        assert not cast(Storage, getattr(video.processed_file, "storage")).exists(
            previous_name
        )
        assert Path(video.processed_file.path).read_bytes().startswith(MAGIC)
        assert not cleanup_receipts(video.meta)
        metadata = video.meta
        assert metadata is not None
        assert metadata["clinical_review"] == {"attempt": attempt}
        assert video.raw_file.name == raw_name
        state = video.get_or_create_state()
        state.anonymized = False
        state.save(update_fields=["anonymized"])


def test_hls_failure_preserves_old_and_new_and_blocks_more_generations(
    video: VideoFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous_name = str(video.processed_file.name)
    monkeypatch.setattr(
        service, "ensure_video_hls", Mock(side_effect=RuntimeError("HLS unavailable"))
    )
    with pytest.raises(RuntimeError, match="Anonymization failed"):
        video.anonymize(delete_original_raw=False)
    video.refresh_from_db()
    published = str(video.processed_file.name)
    assert published != previous_name
    assert cast(Storage, getattr(video.processed_file, "storage")).exists(previous_name)
    assert cast(Storage, getattr(video.processed_file, "storage")).exists(published)
    assert not cleanup_receipts(video.meta)[0].committed
    encoder = Mock()
    monkeypatch.setattr(service, "mask_video_to_roi_and_blacken_intervals", encoder)
    with pytest.raises(RuntimeError, match="cleanup"):
        video.anonymize(delete_original_raw=False)
    encoder.assert_not_called()
    assert video.processed_file.name == published


def test_cleanup_failure_preserves_committed_master_and_retry_reaps_receipt(
    video: VideoFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous_name = str(video.processed_file.name)
    real_delete = cleanup.safe_delete_field_file
    monkeypatch.setattr(
        cleanup, "safe_delete_field_file", Mock(side_effect=OSError("disk failure"))
    )
    assert video.anonymize(delete_original_raw=False)
    video.refresh_from_db()
    published = str(video.processed_file.name)
    assert cast(Storage, getattr(video.processed_file, "storage")).exists(published)
    assert cast(Storage, getattr(video.processed_file, "storage")).exists(previous_name)
    assert cleanup_receipts(video.meta)[0].committed
    monkeypatch.setattr(cleanup, "safe_delete_field_file", real_delete)
    result = cleanup.cleanup_processed_video_generations(video.pk, apply=True)
    assert result.pending == 0
    assert not cast(Storage, getattr(video.processed_file, "storage")).exists(
        previous_name
    )
    video.refresh_from_db()
    assert video.processed_file.name == published


def test_changed_raw_timeline_cannot_rewrite_existing_annotation_coordinates(
    video: VideoFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous_name = str(video.processed_file.name)
    video.fps = 50
    video.save(update_fields=["fps"])
    encoder = Mock()
    monkeypatch.setattr(service, "mask_video_to_roi_and_blacken_intervals", encoder)
    with pytest.raises(RuntimeError, match="Anonymization failed"):
        video.anonymize(delete_original_raw=False)
    encoder.assert_not_called()
    video.refresh_from_db()
    assert video.processed_file.name == previous_name
    assert not cleanup_receipts(video.meta)

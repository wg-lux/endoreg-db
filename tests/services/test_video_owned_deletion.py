from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from endoreg_db.exceptions import MediaOperationDeferred
from endoreg_db.models import Center, MediaOperationLease, UploadJob, VideoFile
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.schemas.processed_video_cleanup import ProcessedGenerationCleanupReceipt
from endoreg_db.services.video_files import deletion
from endoreg_db.utils.file_operations import atomic_write_file
from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.utils.storage.video_fields import VideoArtifactFieldFile

pytestmark = pytest.mark.django_db
SOURCE = b"original video source"


@pytest.fixture
def video() -> VideoFile:
    item = VideoFile.objects.create(
        center=Center.objects.create(name=f"delete-{uuid4().hex}"),
        raw_video_hash=sha256(SOURCE).hexdigest(),
    )
    assert isinstance(item.raw_file, VideoArtifactFieldFile)
    item.raw_file.save(f"{item.raw_video_hash}.mp4", ContentFile(SOURCE))
    return item


@pytest.fixture
def job(video: VideoFile) -> UploadJob:
    return UploadJob.objects.create(
        source_center=video.center,
        content_hash=video.raw_video_hash,
        content_type="video/mp4",
        file=ContentFile(SOURCE, name="original.mp4"),
        status=UploadJob.Status.ERROR,
        error_code=UploadJob.ErrorCode.PROCESSING_FAILED,
        source_file_persisted=True,
        retryable=False,
    )


def test_delete_removes_upload_and_original_drop(
    video: VideoFile, job: UploadJob
) -> None:
    original = get_runtime_paths().watcher_video_drop / "original.mp4"
    atomic_write_file(destination=original, content=[SOURCE])
    job.processing_provenance = {"watched_path": str(original)}
    job.save()
    upload = Path(job.file.path)
    video.delete()
    job.refresh_from_db()
    assert not upload.exists() and not original.exists()
    assert job.cleanup_status == UploadJob.CleanupStatus.COMPLETED
    assert not job.source_file_persisted and not job.file.name
    assert job.status == UploadJob.Status.ERROR


@pytest.mark.parametrize(
    "blocker", ["retry", "processing", "lease", "changed", "outside", "symlink"]
)
def test_source_blockers_preserve_all_files(
    video: VideoFile,
    job: UploadJob,
    blocker: str,
    tmp_path: Path,
) -> None:
    upload = Path(job.file.path)
    if blocker == "retry":
        job.status = UploadJob.Status.RETRYING
        job.retryable = True
        job.next_retry_at = timezone.now() + timedelta(minutes=5)
        job.retry_count = 1
    elif blocker == "processing":
        job.status = UploadJob.Status.PROCESSING
        job.error_code = UploadJob.ErrorCode.NONE
    elif blocker == "lease":
        job.processing_lease_owner = "test-import-worker"
        job.processing_heartbeat_at = timezone.now()
        job.processing_lease_expires_at = timezone.now() + timedelta(minutes=5)
    elif blocker == "changed":
        atomic_write_file(destination=upload, content=[b"different source"])
    elif blocker == "outside":
        job.processing_provenance = {"watched_path": str(tmp_path / "original.mp4")}
    else:
        link = get_runtime_paths().watcher_video_drop / f"{uuid4().hex}.mp4"
        link.symlink_to(upload)
        job.processing_provenance = {"watched_path": str(link)}
    job.save()
    with pytest.raises(ValueError):
        video.delete()
    assert upload.exists()
    assert video.raw_file.storage.exists(str(video.raw_file.name))
    assert VideoFile.objects.filter(pk=video.pk).exists()


def test_playback_blocks_deletion(video: VideoFile, job: UploadJob) -> None:
    MediaOperationLease.objects.create(
        video=video,
        lease_type="stream",
        token=uuid4(),
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    with pytest.raises(MediaOperationDeferred):
        video.delete()
    assert Path(job.file.path).exists()


def test_shared_upload_blocks_deletion(video: VideoFile, job: UploadJob) -> None:
    other = VideoFile.objects.create(
        center=video.center,
        raw_video_hash=sha256(b"other").hexdigest(),
        processed_file=job.file.name,
    )
    with pytest.raises(ValueError, match="shared"):
        video.delete()
    assert Path(job.file.path).exists()
    assert VideoFile.objects.filter(pk=other.pk).exists()


def test_same_filename_different_content_is_preserved(
    video: VideoFile, job: UploadJob
) -> None:
    other = UploadJob.objects.create(
        source_center=video.center,
        content_hash=sha256(b"other").hexdigest(),
        content_type="video/mp4",
        file=ContentFile(b"other", name="original.mp4"),
    )
    other_path = Path(other.file.path)
    video.delete()
    assert other_path.read_bytes() == b"other"


def test_failure_keeps_database_ownership_and_retry_works(
    video: VideoFile,
    job: UploadJob,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_unlink = deletion.safe_unlink_file

    def fail(path: Path, *, missing_ok: bool = True) -> None:
        original_unlink(path, missing_ok=missing_ok)
        raise OSError("interrupted after unlink")

    monkeypatch.setattr(deletion, "safe_unlink_file", fail)
    with pytest.raises(OSError):
        video.delete()
    job.refresh_from_db()
    assert job.file.name and job.source_file_persisted
    assert VideoFile.objects.filter(pk=video.pk).exists()
    monkeypatch.setattr(deletion, "safe_unlink_file", original_unlink)
    video.delete()
    job.refresh_from_db()
    assert not job.source_file_persisted


def test_receipts_remove_old_and_intermediate_generations_and_failed_hls(
    video: VideoFile,
) -> None:
    field = video.processed_file
    assert isinstance(field, VideoArtifactFieldFile)
    names: list[str] = []
    for payload in (b"old", b"intermediate", b"current"):
        field.save(f"{video.raw_video_hash}.{uuid4().hex}.mp4", ContentFile(payload))
        names.append(str(field.name))
    receipt = ProcessedGenerationCleanupReceipt(
        source_name=names[0],
        source_sha256=sha256(b"old").hexdigest(),
        replacement_name=names[1],
        replacement_sha256=sha256(b"intermediate").hexdigest(),
        committed=True,
    )
    video.meta = {"processed_generation_cleanup": [receipt.model_dump(mode="json")]}
    video.save()
    artifact = VideoHlsArtifact.objects.create(
        video=video,
        artifact_kind="processed",
        status="failed",
        error_code=VideoHlsArtifact.ErrorCode.MATERIALIZATION_FAILED,
    )
    segment = (
        get_runtime_paths().streamable_videos_processed_media
        / "hls"
        / str(video.uuid)
        / str(artifact.key_id)
        / "v0"
        / "segment.ts"
    )
    atomic_write_file(destination=segment, content=[b"segment"])
    video.delete()
    assert all(not field.storage.exists(name) for name in names)
    assert not segment.exists()
    assert not VideoHlsArtifact.objects.filter(pk=artifact.pk).exists()

from datetime import timedelta
from hashlib import sha256
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.db.models.fields.files import FieldFile
from django.utils import timezone
from lx_dtypes.models.contracts.hub_media_envelope import HubMediaEnvelopeReceipt

from endoreg_db.models import (
    Center,
    Frame,
    MediaOperationLease,
    NetworkNode,
    VideoFile,
    UploadJob,
)
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.models.hub.outbound_hub_transfer_job import OutboundHubTransferJob
from endoreg_db.services.hub.transfer_cleanup import cleanup_transferred_video_files
from endoreg_db.services.video_files import io
from endoreg_db.utils.file_operations import atomic_write_file
from endoreg_db.utils.paths import get_runtime_paths, to_protected_media_relative

pytestmark = pytest.mark.django_db


@pytest.fixture
def completed_video() -> tuple[OutboundHubTransferJob, HubMediaEnvelopeReceipt]:
    center = Center.objects.create(name="cleanup", center_key="cleanup")
    hub = NetworkNode.objects.create(
        node_key="hub",
        display_name="hub",
        role="central_hub",
    )
    digest = sha256(b"processed").hexdigest()
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash=sha256(b"raw").hexdigest(),
        processed_video_hash=digest,
        raw_file=ContentFile(b"raw", name="raw.mp4"),
        processed_file=ContentFile(b"processed", name="processed.mp4"),
    )
    receipt = HubMediaEnvelopeReceipt(
        transfer_key="cleanup-video",
        source_node_key="site",
        source_center_key="cleanup",
        target_node_key="hub",
        resource_kind="video",
        resource_hash=video.raw_video_hash,
        processed_media_hash=digest,
        plaintext_sha256=digest,
        plaintext_size=9,
        recipient_key_id="a" * 64,
        ciphertext_sha256="b" * 64,
        ciphertext_size=9,
        envelope_fingerprint_sha256="c" * 64,
        receiver_transfer_id="remote-video",
        processing_decision="skip_processing_preserved_state",
    )
    job = OutboundHubTransferJob.objects.create(
        video_file=video,
        source_center=center,
        target_node=hub,
        resource_kind="video",
        transfer_key=receipt.transfer_key,
        local_status="completed",
        remote_transfer_status="applied",
        remote_transfer_id=receipt.receiver_transfer_id,
        local_cleanup_status="retained",
        envelope_receipt=receipt.model_dump(mode="json"),
    )
    return job, receipt


def test_removes_all_owned_files_retains_records_and_replays(
    completed_video: tuple[OutboundHubTransferJob, HubMediaEnvelopeReceipt],
) -> None:
    job, receipt = completed_video
    video = job.video_file
    assert video is not None
    raw_name, processed_name = video.raw_file.name, video.processed_file.name
    assert raw_name and processed_name
    paths = get_runtime_paths()
    upload = UploadJob.objects.create(
        source_center=video.center,
        content_hash=video.raw_video_hash,
        content_type="video/mp4",
        file=ContentFile(b"raw", name="upload.mp4"),
        status=UploadJob.Status.ERROR,
        error_code=UploadJob.ErrorCode.PROCESSING_FAILED,
        retryable=False,
    )
    owned_paths = [
        paths.transcoding
        / "processed_storage_pressure"
        / str(video.uuid)
        / "candidate.mp4",
        paths.streamable_videos_raw_media / "hls" / str(video.uuid) / "segment.ts",
        paths.streamable_videos_processed_media
        / "hls"
        / str(video.uuid)
        / "segment.ts",
        paths.streamable_videos_raw_media / "raw-playback.mp4",
        paths.streamable_videos_processed_media / "processed-playback.mp4",
    ]
    for path in owned_paths:
        atomic_write_file(destination=path, content=[b"playback"])
    video.raw_streamable_relative_path = to_protected_media_relative(owned_paths[-2])
    video.processed_streamable_relative_path = to_protected_media_relative(
        owned_paths[-1]
    )
    video.save(
        update_fields=[
            "raw_streamable_relative_path",
            "processed_streamable_relative_path",
        ]
    )
    frame = Frame.objects.create(
        video=video, frame_number=0, relative_path="frame.jpg", is_extracted=True
    )
    atomic_write_file(destination=frame.file_path, content=[b"frame"])
    cleanup_transferred_video_files(outbound_job_id=str(job.pk), receipt=receipt)
    cleanup_transferred_video_files(outbound_job_id=str(job.pk), receipt=receipt)
    video.refresh_from_db()
    job.refresh_from_db()
    frame.refresh_from_db()
    assert not video.raw_file.name and not video.processed_file.name
    assert not video.raw_file.storage.exists(raw_name)
    assert not video.processed_file.storage.exists(processed_name)
    assert not frame.file_path.exists() and not frame.is_extracted
    assert job.envelope_receipt == receipt.model_dump(mode="json")
    assert job.local_files_cleanup_completed_at is not None
    assert video.processed_video_hash == receipt.plaintext_sha256
    assert all(not path.exists() for path in owned_paths)
    upload.refresh_from_db()
    assert not upload.file.name


def test_partial_deletion_retries_from_durable_intent(
    completed_video: tuple[OutboundHubTransferJob, HubMediaEnvelopeReceipt],
) -> None:
    job, receipt = completed_video
    original = io.delete_field_file

    def interrupt_processed(field: FieldFile, *, missing_ok: bool, save: bool) -> bool:
        if field.field.name == "processed_file":
            raise OSError("storage unavailable")
        return original(field, missing_ok=missing_ok, save=save)

    with patch.object(io, "delete_field_file", side_effect=interrupt_processed):
        with pytest.raises(OSError, match="storage unavailable"):
            cleanup_transferred_video_files(
                outbound_job_id=str(job.pk), receipt=receipt
            )
    job.refresh_from_db()
    assert job.local_files_cleanup_started_at is not None
    assert job.local_files_cleanup_completed_at is None
    cleanup_transferred_video_files(outbound_job_id=str(job.pk), receipt=receipt)
    job.refresh_from_db()
    assert job.local_files_cleanup_completed_at is not None


@pytest.mark.parametrize(
    "blocker",
    [
        "receipt",
        "incomplete",
        "changed",
        "lease",
        "shared",
        "other_transfer",
        "queued_playback",
    ],
)
def test_rejects_unsafe_cleanup_before_file_removal(
    completed_video: tuple[OutboundHubTransferJob, HubMediaEnvelopeReceipt],
    blocker: str,
) -> None:
    job, receipt = completed_video
    video = job.video_file
    assert video is not None
    raw_name = video.raw_file.name
    assert raw_name
    if blocker == "receipt":
        receipt = receipt.model_copy(update={"receiver_transfer_id": "other"})
    elif blocker == "incomplete":
        OutboundHubTransferJob.objects.filter(pk=job.pk).update(
            local_status="uploading"
        )
    elif blocker == "changed":
        video.processed_file.save("changed.mp4", ContentFile(b"different"))
    elif blocker == "lease":
        MediaOperationLease.objects.create(
            video=video,
            lease_type="stream",
            expires_at=timezone.now() + timedelta(minutes=1),
        )
    elif blocker == "shared":
        VideoFile.objects.create(
            center=video.center, raw_video_hash="other", raw_file=raw_name
        )
    elif blocker == "queued_playback":
        VideoHlsArtifact.objects.create(
            video=video, artifact_kind="processed", status="queued"
        )
    else:
        hub = NetworkNode.objects.create(
            node_key="other", display_name="other", role="central_hub"
        )
        OutboundHubTransferJob.objects.create(
            video_file=video,
            source_center=video.center,
            target_node=hub,
            resource_kind="video",
            transfer_key="other-transfer",
            local_status="queued",
        )
    with pytest.raises((ValueError, RuntimeError)):
        cleanup_transferred_video_files(outbound_job_id=str(job.pk), receipt=receipt)
    assert video.raw_file.storage.exists(raw_name)
    job.refresh_from_db()
    assert job.local_files_cleanup_completed_at is None

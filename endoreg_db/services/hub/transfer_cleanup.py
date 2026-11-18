"""Explicit site file reclamation backed by a freshly verified hub receipt."""

from django.db import transaction
from django.utils import timezone
from lx_dtypes.models.contracts.hub_media_envelope import HubMediaEnvelopeReceipt

from endoreg_db.models.hub.outbound_hub_transfer_job import OutboundHubTransferJob
from endoreg_db.models.hub.transfer_job import TransferJob
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.models.media.video.video_transcode_job import VideoTranscodeJob
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.services.hub.deployment import hub_mode_enabled
from endoreg_db.services.hub.transfers import get_media_envelope_receipt
from endoreg_db.services.media.operation_gate import defer_if_video_media_busy
from endoreg_db.services.video_files.io import delete_video_owned_files
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.storage_streaming import field_file_size


def _verify_master(video: VideoFile, receipt: HubMediaEnvelopeReceipt) -> None:
    if (
        not video.processed_file.name
        or video.processed_video_hash != receipt.plaintext_sha256
        or field_file_size(video.processed_file) != receipt.plaintext_size
        or get_file_hash(video.processed_file) != receipt.plaintext_sha256
    ):
        raise ValueError("Current processed video does not match the hub receipt.")


@transaction.atomic
def verify_received_video_media(transfer_job: TransferJob) -> None:
    """Re-read the accepted master, holding the publication/deletion row lock."""
    transfer_job = TransferJob.objects.select_for_update().get(pk=transfer_job.pk)
    receipt = get_media_envelope_receipt(transfer_job)
    if (
        transfer_job.resource_kind != TransferJob.ResourceKind.VIDEO
        or transfer_job.transfer_status != TransferJob.TransferStatus.APPLIED
        or receipt is None
        or receipt.receiver_transfer_id != str(transfer_job.pk)
        or receipt.transfer_key != transfer_job.transfer_key
    ):
        raise ValueError("Applied video transfer receipt is required.")
    video = VideoFile.objects.select_for_update().get(
        pk=transfer_job.target_object_id, center_id=transfer_job.source_center_id
    )
    _verify_master(video, receipt)


def cleanup_transferred_video_files(
    *, outbound_job_id: str, receipt: HubMediaEnvelopeReceipt
) -> None:
    """Caller authenticates a fresh hub media verification before invoking this.

    Persist intent before filesystem changes. A retry may then finish after an
    unlink succeeded but the final transaction failed. Clinical rows, hashes,
    annotations and the immutable receipt survive local file reclamation.
    """
    if hub_mode_enabled():
        raise ValueError("Local video reclamation is forbidden on a central hub.")
    with transaction.atomic():
        job, video = _locked_cleanup_target(outbound_job_id, receipt)
        if job.local_files_cleanup_completed_at is not None:
            return
        if job.local_files_cleanup_started_at is None:
            # A previous processed-only reaper has already authenticated removal.
            if job.local_cleanup_status != str(
                OutboundHubTransferJob.LocalCleanupStatus.CLEANED
            ):
                _verify_master(video, receipt)
            job.local_files_cleanup_started_at = timezone.now()
            job.save(update_fields=["local_files_cleanup_started_at", "updated_at"])

    with transaction.atomic():
        job, video = _locked_cleanup_target(outbound_job_id, receipt)
        if job.local_files_cleanup_completed_at is not None:
            return
        if video.processed_file.name and video.processed_file.storage.exists(
            video.processed_file.name
        ):
            _verify_master(video, receipt)
        delete_video_owned_files(video, missing_ok=True)
        video.raw_file.name = ""
        video.processed_file.name = ""
        video.raw_streamable_relative_path = ""
        video.processed_streamable_relative_path = ""
        meta = dict(video.meta or {})
        meta.pop("processed_generation_cleanup", None)
        video.meta = meta
        video.save(
            update_fields=[
                "raw_file",
                "processed_file",
                "raw_streamable_relative_path",
                "processed_streamable_relative_path",
                "meta",
                "date_modified",
            ]
        )
        Frame.objects.filter(video=video).update(is_extracted=False)
        job.local_cleanup_status = OutboundHubTransferJob.LocalCleanupStatus.CLEANED
        job.local_cleanup_completed_at = timezone.now()
        job.local_files_cleanup_completed_at = job.local_cleanup_completed_at
        job.save(
            update_fields=[
                "local_cleanup_status",
                "local_cleanup_completed_at",
                "local_files_cleanup_completed_at",
                "updated_at",
            ]
        )


def _locked_cleanup_target(
    job_id: str, receipt: HubMediaEnvelopeReceipt
) -> tuple[OutboundHubTransferJob, VideoFile]:
    job = OutboundHubTransferJob.objects.select_for_update().get(pk=job_id)
    if (
        job.resource_kind != str(OutboundHubTransferJob.ResourceKind.VIDEO)
        or job.local_status != str(OutboundHubTransferJob.LocalStatus.COMPLETED)
        or job.remote_transfer_status != "applied"
        or receipt.receiver_transfer_id != job.remote_transfer_id
        or receipt.model_dump(mode="json") != job.envelope_receipt
    ):
        raise ValueError(
            "Completed video and matching verified hub receipt are required."
        )
    video = VideoFile.objects.select_for_update().get(pk=job.video_file_id)
    if video.center_id != job.source_center_id:
        raise ValueError("Video ownership changed after transfer.")
    job.video_file = video
    job.full_clean()
    defer_if_video_media_busy(video_id=int(video.pk))
    if (
        VideoHlsArtifact.objects.filter(
            video=video,
            status__in=[
                VideoHlsArtifact.Status.QUEUED,
                VideoHlsArtifact.Status.MATERIALIZING,
                VideoHlsArtifact.Status.VALIDATED,
            ],
        ).exists()
        or VideoTranscodeJob.objects.filter(
            video=video,
            status__in=[
                VideoTranscodeJob.Status.QUEUED,
                VideoTranscodeJob.Status.RUNNING,
            ],
        ).exists()
    ):
        raise ValueError(
            "Active playback preparation or transcoding blocks local cleanup."
        )
    if VideoProcessingHistory.objects.filter(
        video=video,
        status__in=[
            VideoProcessingHistory.STATUS_PENDING,
            VideoProcessingHistory.STATUS_RUNNING,
        ],
    ).exists():
        raise ValueError("Active video processing blocks local cleanup.")
    if (
        OutboundHubTransferJob.objects.filter(video_file=video)
        .exclude(local_status__in=[job.LocalStatus.COMPLETED, job.LocalStatus.FAILED])
        .exists()
    ):
        raise ValueError("Another outbound transfer still needs the local video.")
    return job, video

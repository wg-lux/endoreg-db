"""Operator acknowledgment of usable media whose historical frame mapping is lost."""

from __future__ import annotations

from django.db import transaction
from lx_dtypes.models.contracts.video_provenance import (
    RecordingTimeRange,
    VideoFrameTimeline,
    VideoProvenanceRecovery,
)

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.models.state.audit_ledger import AuditLedger
from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.services.media.operation_gate import video_artifact_mutation
from endoreg_db.services.video_storage.canonical_timelines import (
    capture_canonical_timeline,
)
from endoreg_db.services.video_storage.frame_provenance import provenance_chain
from endoreg_db.services.video_storage.probes import probe_video_artifact
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.storage import ensure_local_file


class ActiveVideoProcessing(ValueError):
    """Recovery must wait for an existing processing job to finish."""


def recover_video_provenance(
    video_id: int,
    *,
    actor_id: int,
    dry_run: bool,
    recording_started_at: str | None = None,
    recording_ended_at: str | None = None,
) -> VideoProvenanceRecovery:
    """Recheck media and preserve evidence; never fabricate frame ancestry or approval."""
    RecordingTimeRange(
        recording_started_at=recording_started_at,
        recording_ended_at=recording_ended_at,
    )
    with video_artifact_mutation(video_id=video_id), transaction.atomic():
        video = (
            VideoFile.objects.select_for_update(of=("self",))
            .select_related("state")
            .get(pk=video_id)
        )
        state = video.state
        meta = VideoFileMetaPayload.model_validate(video.meta or {})
        previous = meta.video_provenance_recovery
        recording_times = RecordingTimeRange(
            recording_started_at=recording_started_at
            or (previous.recording_started_at if previous else None),
            recording_ended_at=recording_ended_at
            or (previous.recording_ended_at if previous else None),
        )
        if state is None or not state.anonymized or state.processing_error:
            raise ValueError(
                "Processed media must exist without a processing integrity error"
            )
        if meta.integrity_status == "lost":
            raise ValueError(
                "Media integrity must be restored before provenance recovery"
            )
        if VideoProcessingHistory.objects.filter(
            video=video, status__in=["pending", "running"]
        ).exists():
            raise ActiveVideoProcessing(
                "Wait for active video processing before provenance recovery"
            )
        # Verified mappings remain authoritative. A stale or incomplete mapping is
        # retained in metadata, but the separate acknowledgment governs this generation.
        existing = meta.video_frame_provenance
        if (
            existing is not None
            and existing.transformations[-1].output.content_hash
            == video.processed_video_hash
        ):
            raise ValueError(
                "Verified frame provenance already exists for this generation"
            )
        try:
            provenance_chain(
                source_hash=video.raw_video_hash, output_hash=video.processed_video_hash
            )
        except ValueError:
            pass  # Explicit operator-selected unresolved recovery, never an automatic fallback.
        else:
            raise ValueError(
                "Verified frame provenance already exists for this generation"
            )
        if (
            not video.processed_file
            or get_file_hash(video.processed_file) != video.processed_video_hash
        ):
            raise ValueError(
                "Processed media hash does not match; provenance recovery cannot approve changed bytes"
            )
        with ensure_local_file(video.processed_file) as path:
            processed = capture_canonical_timeline(
                path, probe_video_artifact(path), require_original_pts=True
            )
        if processed.content_hash != video.processed_video_hash:
            raise ValueError("Processed media changed during provenance recovery")
        original: VideoFrameTimeline | None = None
        for entry in meta.canonical_timeline_history or []:
            if entry.before.content_hash == video.raw_video_hash:
                original = entry.before
                break
        if (
            original is None
            and video.raw_file
            and video.raw_file.storage.exists(str(video.raw_file.name))
        ):
            with ensure_local_file(video.raw_file) as path:
                if get_file_hash(path) == video.raw_video_hash:
                    original = capture_canonical_timeline(
                        path, probe_video_artifact(path), require_original_pts=True
                    )
                else:
                    raise ValueError(
                        "Retained original media hash does not match the recording"
                    )
        if original is None and previous is not None:
            original = previous.original_timeline
        receipt = VideoProvenanceRecovery(
            recording_hash=video.raw_video_hash,
            original_timeline=original,
            processed_timeline=processed,
            recording_started_at=recording_times.recording_started_at,
            recording_ended_at=recording_times.recording_ended_at,
        )
        if not dry_run and receipt != previous:
            data = receipt.model_dump(mode="json")
            video.meta = {**(video.meta or {}), "video_provenance_recovery": data}
            video.save(update_fields=["meta", "date_modified"])
            AuditLedger.objects.create(
                user_id=actor_id,
                object_type="VideoFile",
                object_pk=str(video.pk),
                action="acknowledge_unresolved_provenance",
                data={
                    "before": previous.model_dump(mode="json") if previous else None,
                    "after": data,
                },
            )
        return receipt

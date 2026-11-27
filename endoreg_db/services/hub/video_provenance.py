"""Privacy-limited video ancestry exchange and explicit independent-clock joins."""

from __future__ import annotations

import logging
from bisect import bisect_left
from fractions import Fraction
from typing import TYPE_CHECKING

from django.db import transaction
from lx_dtypes.evaluation.electrical import recording_to_electrical_ticks
from lx_dtypes.models.contracts.video_provenance import (
    RecordingSynchronization,
    ElectricalTimestampSeries,
    VideoFrameTimeline,
    VideoProvenanceBundle,
    VideoProvenanceRecovery,
)

from endoreg_db.utils.structured_logging import emit_structured_event
from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.services.video_storage.frame_provenance import (
    MissingFrameAncestry,
    resolve_video_provenance,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def _recorded_provenance(
    video: VideoFile,
) -> VideoProvenanceBundle | VideoProvenanceRecovery | None:
    try:
        return resolve_video_provenance(video)
    except MissingFrameAncestry:
        return None


def export_video_provenance(
    video: VideoFile,
    *,
    require_hub_approval: bool = False,
) -> VideoProvenanceBundle | VideoProvenanceRecovery:
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.services.media.export_downloads import validate_video_download
    from endoreg_db.services.media.operation_gate import (
        assert_video_not_transcoding,
        create_video_stream_lease,
    )

    # Optimistically read immutable generation bytes. Publication and approval
    # are checked again on a fresh row before the reader lease is granted.
    assert_video_not_transcoding(video.pk)
    recorded = _recorded_provenance(video)
    # Timestamp capture authenticates the decoded file and computes its content hash.
    validate_video_download(video, verify_content=recorded is not None)
    result = recorded
    if result is None:
        processed = _decode_processed_timeline(video)
        if processed.content_hash != video.processed_video_hash:
            raise ValueError("Decoded timeline belongs to another processed generation")
        result = VideoProvenanceRecovery(
            method="decoded_processed_timeline_v1",
            recording_hash=video.raw_video_hash,
            processed_timeline=processed,
        )

    with transaction.atomic():
        current = VideoFile.objects.select_for_update().get(pk=video.pk)
        if (
            current.center_id,
            current.raw_video_hash,
            current.processed_video_hash,
            current.processed_file.name,
        ) != (
            video.center_id,
            video.raw_video_hash,
            video.processed_video_hash,
            video.processed_file.name,
        ):
            raise ValueError(
                "Processed generation changed while preparing provenance export"
            )
        assert_video_not_transcoding(current.pk)
        if require_hub_approval:
            from endoreg_db.services.hub.outbound_export_readiness import (
                resolve_video_hub_export_state,
            )
            from endoreg_db.services.media.export_ready import ReadyForExportError

            readiness = resolve_video_hub_export_state(current)
            if not readiness.transfer_eligible:
                raise ReadyForExportError(readiness.transfer_validation_error)
        validate_video_download(current, verify_content=False)
        if _recorded_provenance(current) != recorded:
            raise ValueError("Frame evidence changed during transfer preparation")
        create_video_stream_lease(current, file_type="processed")
    if recorded is None and isinstance(result, VideoProvenanceRecovery):
        emit_structured_event(
            logging.getLogger(__name__),
            "hub_transfer.processed_timeline_selected",
            video_id=current.pk,
            recording_hash=current.raw_video_hash,
            processed_hash=current.processed_video_hash,
            method=result.method,
        )
    return result


def accept_video_provenance(video: VideoFile, payload: object) -> None:
    """Called in the authenticated, center-owned transfer transaction.

    Receipt is not media validation: electrical joins separately require the
    current processed file to be present and hash-verified.
    """
    proof = VideoProvenanceBundle.model_validate(payload)
    if proof.transformations[0].source.content_hash != video.raw_video_hash:
        raise ValueError("Transferred frame provenance belongs to another recording")
    if proof.transformations[-1].output.content_hash != video.processed_video_hash:
        raise ValueError(
            "Transferred frame provenance does not match processed media identity"
        )
    meta = dict(video.meta or {})
    meta.pop("video_provenance_recovery", None)
    meta["video_frame_provenance"] = proof.model_dump(mode="json")
    video.meta = meta


def accept_video_provenance_recovery(video: VideoFile, payload: object) -> None:
    """Persist an explicit unresolved receipt after binding both media identities."""
    receipt = VideoProvenanceRecovery.model_validate(payload)
    if receipt.recording_hash != video.raw_video_hash or (
        receipt.processed_timeline.content_hash != video.processed_video_hash
    ):
        raise ValueError("Unresolved provenance receipt belongs to different media")
    video.meta = {
        **(video.meta or {}),
        "video_provenance_recovery": receipt.model_dump(mode="json"),
    }


def _decode_processed_timeline(video: VideoFile) -> VideoFrameTimeline:
    from endoreg_db.services.video_storage.canonical_timelines import (
        capture_canonical_timeline,
    )
    from endoreg_db.services.video_storage.probes import probe_video_artifact
    from endoreg_db.utils.storage import ensure_local_file

    with ensure_local_file(video.processed_file) as path:
        return capture_canonical_timeline(
            path, probe_video_artifact(path), require_original_pts=True
        )


def validate_received_video_timeline(
    video: VideoFile, proof: VideoProvenanceBundle | None = None
) -> None:
    recovery = VideoFileMetaPayload.model_validate(
        video.meta or {}
    ).video_provenance_recovery
    if recovery is not None:
        if (
            recovery.recording_hash != video.raw_video_hash
            or recovery.processed_timeline.content_hash != video.processed_video_hash
        ):
            raise ValueError("Recovered provenance does not match received media")
        if _decode_processed_timeline(video) != recovery.processed_timeline:
            raise ValueError("Recovered timestamps disagree with received media")
        return
    if proof is None:
        proof = VideoFileMetaPayload.model_validate(
            video.meta or {}
        ).video_frame_provenance
    if proof is None:
        return  # Legacy transfers remain readable but cannot supply electrical alignment.
    if _decode_processed_timeline(video) != proof.transformations[-1].output:
        raise ValueError(
            "Received frame provenance disagrees with decoded processed media"
        )


def record_video_synchronization(
    video: VideoFile, synchronization: RecordingSynchronization
) -> None:
    """Attach acquisition-provided anchors, never infer synchronization from media."""
    from endoreg_db.models.media.video.video_file import VideoFile

    with transaction.atomic():
        current = VideoFile.objects.select_for_update().get(pk=video.pk)
        meta = VideoFileMetaPayload.model_validate(current.meta or {})
        if synchronization.source_content_hash != current.raw_video_hash:
            raise ValueError("Synchronization source does not match the recording")
        if (
            meta.recording_synchronization is not None
            and meta.recording_synchronization != synchronization
        ):
            raise ValueError(
                "Recording synchronization is already bound; conflicting replacement rejected"
            )
        updated = dict(current.meta or {})
        if meta.video_frame_provenance is not None:
            previous_sync = meta.video_frame_provenance.synchronization
            if previous_sync is not None and previous_sync != synchronization:
                raise ValueError(
                    "Imported recording synchronization conflicts with supplied anchors"
                )
            updated["video_frame_provenance"] = VideoProvenanceBundle(
                transformations=meta.video_frame_provenance.transformations,
                synchronization=synchronization,
            ).model_dump(mode="json")
        else:
            updated["recording_synchronization"] = synchronization.model_dump(
                mode="json"
            )
        current.meta = updated
        current.save(update_fields=["meta"])
        video.meta = current.meta


def electrical_sample_indices(
    proof: VideoProvenanceBundle,
    *,
    samples: ElectricalTimestampSeries,
    max_error_seconds: Fraction,
) -> list[int]:
    """Return one electrical sample index per output frame, within explicit bounds.

    Linear interpolation applies only between measured synchronization anchors.
    Acquisition must supply a conservative error bound for that interval. Ties,
    extrapolation and an unproven clock association are rejected.
    """
    sync = proof.synchronization
    if sync is None:
        raise ValueError("Independent clocks require recording synchronization anchors")
    if (
        sync.session_id != samples.session_id
        or sync.electrical_clock_id != samples.clock_id
    ):
        raise ValueError("Electrical data belongs to a different session or clock")
    if max_error_seconds < 0:
        raise ValueError("Maximum alignment error must be nonnegative")
    if Fraction(samples.time_base_num, samples.time_base_den) != Fraction(
        sync.electrical_time_base_num, sync.electrical_time_base_den
    ):
        raise ValueError(
            "Electrical timestamp units differ from the synchronized device clock"
        )
    electrical_timestamps = samples.presentation_timestamps
    source_indices = list(
        range(len(proof.transformations[-1].output.presentation_timestamps))
    )
    for edge in reversed(proof.transformations):
        source_indices = [edge.source_frame_indices[index] for index in source_indices]
    source_ticks = proof.transformations[0].source.presentation_timestamps
    electrical_base = Fraction(
        sync.electrical_time_base_num, sync.electrical_time_base_den
    )
    uncertainty = Fraction(sync.uncertainty_num, sync.uncertainty_den)
    selected: list[int] = []
    for source_index in source_indices:
        tick = source_ticks[source_index]
        target = recording_to_electrical_ticks(sync, tick)
        if not electrical_timestamps[0] <= target <= electrical_timestamps[-1]:
            raise ValueError("Synchronized frame lies outside the electrical recording")
        position = bisect_left(electrical_timestamps, target)
        candidates = range(
            max(0, position - 1), min(len(electrical_timestamps), position + 1)
        )
        distances = [
            (abs(electrical_timestamps[index] - target), index) for index in candidates
        ]
        distances.sort()
        if len(distances) > 1 and distances[0][0] == distances[1][0]:
            raise ValueError("Electrical sample association is ambiguous")
        distance, chosen = distances[0]
        for neighbor in (chosen - 1, chosen + 1):
            if 0 <= neighbor < len(electrical_timestamps):
                margin = abs(electrical_timestamps[neighbor] - target) - distance
                if margin * electrical_base <= 2 * uncertainty:
                    raise ValueError(
                        "Clock uncertainty makes the electrical sample association ambiguous"
                    )
        if distance * electrical_base + uncertainty > max_error_seconds:
            raise ValueError(
                "Electrical sample association exceeds the alignment error budget"
            )
        selected.append(chosen)
    return selected


def map_video_to_electrical_samples(
    video: VideoFile,
    *,
    samples: ElectricalTimestampSeries,
    max_error_seconds: Fraction,
) -> list[int]:
    proof = export_video_provenance(video)
    if isinstance(proof, VideoProvenanceRecovery):
        raise ValueError(
            "Electrical matching is unavailable: frame mapping is unresolved"
        )
    return electrical_sample_indices(
        proof,
        samples=samples,
        max_error_seconds=max_error_seconds,
    )

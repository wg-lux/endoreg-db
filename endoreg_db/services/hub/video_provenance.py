"""Privacy-limited video ancestry exchange and explicit independent-clock joins."""

from __future__ import annotations

from bisect import bisect_left
from fractions import Fraction
from typing import TYPE_CHECKING

from django.db import transaction
from lx_dtypes.models.contracts.video_provenance import (
    RecordingSynchronization,
    ElectricalTimestampSeries,
    VideoProvenanceBundle,
)

from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.services.video_storage.frame_provenance import provenance_chain

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def export_video_provenance(video: VideoFile) -> VideoProvenanceBundle:
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.services.media.operation_gate import create_video_stream_lease

    with transaction.atomic():
        current = VideoFile.objects.select_for_update().get(pk=video.pk)
        if (current.processed_video_hash, current.processed_file.name) != (
            video.processed_video_hash,
            video.processed_file.name,
        ):
            raise ValueError(
                "Processed generation changed while preparing provenance export"
            )
        result = _export_current_video_provenance(current)
        create_video_stream_lease(current, file_type="processed")
        return result


def _export_current_video_provenance(video: VideoFile) -> VideoProvenanceBundle:
    """Export only an approved, hash-verified processed artifact's ancestry."""
    from endoreg_db.services.media.export_downloads import validate_video_download

    validate_video_download(video)
    meta = VideoFileMetaPayload.model_validate(video.meta or {})
    existing = meta.video_frame_provenance
    if existing is not None:
        previous_hash = existing.transformations[-1].output.content_hash
        if previous_hash != video.processed_video_hash:
            continuation = provenance_chain(
                source_hash=previous_hash, output_hash=video.processed_video_hash
            )
            existing = VideoProvenanceBundle(
                transformations=[
                    *existing.transformations,
                    *continuation.transformations,
                ],
                synchronization=existing.synchronization,
            )
        validate_received_video_timeline(video, existing)
        return existing
    history = meta.canonical_timeline_history or []
    source_hash = history[0].before.content_hash if history else video.raw_video_hash
    bundle = provenance_chain(
        source_hash=source_hash, output_hash=video.processed_video_hash
    )
    return VideoProvenanceBundle(
        transformations=bundle.transformations,
        synchronization=meta.recording_synchronization,
    )


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
    meta["video_frame_provenance"] = proof.model_dump(mode="json")
    video.meta = meta


def validate_received_video_timeline(
    video: VideoFile, proof: VideoProvenanceBundle | None = None
) -> None:
    from endoreg_db.services.video_storage.canonical_timelines import (
        capture_canonical_timeline,
    )
    from endoreg_db.services.video_storage.probes import probe_video_artifact
    from endoreg_db.utils.storage import ensure_local_file

    if proof is None:
        proof = VideoFileMetaPayload.model_validate(
            video.meta or {}
        ).video_frame_provenance
    if proof is None:
        return  # Legacy transfers remain readable but cannot supply electrical alignment.
    with ensure_local_file(video.processed_file) as path:
        actual = capture_canonical_timeline(
            path, probe_video_artifact(path), require_original_pts=True
        )
    if actual != proof.transformations[-1].output:
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
        history = meta.canonical_timeline_history or []
        source_hash = (
            history[0].before.content_hash if history else current.raw_video_hash
        )
        if synchronization.source_content_hash != source_hash:
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
    anchors = sync.anchors
    video_anchors = [anchor.video_timestamp for anchor in anchors]
    electrical_base = Fraction(
        sync.electrical_time_base_num, sync.electrical_time_base_den
    )
    uncertainty = Fraction(sync.uncertainty_num, sync.uncertainty_den)
    selected: list[int] = []
    for source_index in source_indices:
        tick = source_ticks[source_index]
        if not video_anchors[0] <= tick <= video_anchors[-1]:
            raise ValueError(
                "Video frame lies outside the measured synchronization interval"
            )
        right = min(max(1, bisect_left(video_anchors, tick)), len(anchors) - 1)
        before, after = anchors[right - 1], anchors[right]
        target = before.electrical_timestamp + Fraction(
            (tick - before.video_timestamp)
            * (after.electrical_timestamp - before.electrical_timestamp),
            after.video_timestamp - before.video_timestamp,
        )
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
    return electrical_sample_indices(
        export_video_provenance(video),
        samples=samples,
        max_error_seconds=max_error_seconds,
    )

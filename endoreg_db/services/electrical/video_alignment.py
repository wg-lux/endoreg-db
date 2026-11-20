"""On-demand, bounded alignment of an imported series to the published generation.

The canonical algorithm is lx_dtypes ``align_electrosurgical_measurements``
(``electrical_frame_window_v1``). This module only resolves its inputs: the
verified frame ancestry of the *current* processed generation, the recording
synchronization anchors, the decrypted measurement series and its aggregation
profile. Nothing is persisted or cached; a result is recomputed from the
current identities on every request, so a new generation or measurement can
never reuse an earlier alignment. Presentation timestamps (PTS) are
authoritative; nominal frames per second are never consulted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, NoReturn

from lx_dtypes.models.contracts.electrical_video_alignment import (
    ElectricalAlignmentError,
    ElectricalVideoAlignment,
    align_electrosurgical_measurements,
)
from lx_dtypes.models.contracts.electrosurgical_measurements import (
    ElectrosurgicalMeasurementSeries,
)
from lx_dtypes.models.contracts.video_provenance import (
    VideoProvenanceBundle,
    VideoProvenanceRecovery,
)

from endoreg_db.models.media.video.electrosurgical_measurement import (
    VideoElectrosurgicalMeasurement,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.schemas.electrosurgical_measurement import (
    parse_aggregation_profile,
    parse_channel_descriptors,
)
from endoreg_db.services.electrical.measurement_import import (
    MAX_ELECTROSURGICAL_SOURCE_BYTES,
    ElectrosurgicalImportError,
    validate_series_bytes,
)
from endoreg_db.services.video_storage.frame_provenance import (
    resolve_video_provenance,
)
from endoreg_db.utils.hashs import get_bytes_hash
from endoreg_db.utils.storage_streaming import field_file_size, iter_field_file_bytes
from endoreg_db.utils.structured_logging import emit_structured_event
from endoreg_db.utils.video.timeline import presentation_timestamp_seconds

logger = logging.getLogger(__name__)

MAX_ALIGNMENT_FRAMES_PER_REQUEST: Final = 5000


class ElectricalAlignmentRefusal(StrEnum):
    """Documented reasons why no aligned frame is returned."""

    STALE_GENERATION = "stale_generation"
    RECORDING_MISMATCH = "recording_mismatch"
    PROVENANCE_UNRESOLVED = "provenance_unresolved"
    PROVENANCE_UNAVAILABLE = "provenance_unavailable"
    SYNCHRONIZATION_MISSING = "synchronization_missing"
    ALIGNMENT_REJECTED = "alignment_rejected"
    SOURCE_INTEGRITY_FAILED = "source_integrity_failed"
    UNKNOWN_CHANNEL = "unknown_channel"
    INVALID_RANGE = "invalid_range"


class ElectricalAlignmentUnavailable(Exception):
    """A precondition failed; callers must not present partial results."""

    def __init__(self, code: ElectricalAlignmentRefusal, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ElectricalAlignmentEligibility:
    eligible: bool
    refusal: ElectricalAlignmentRefusal | None
    message: str | None
    total_frame_count: int | None


@dataclass(frozen=True, slots=True)
class ElectricalAlignmentPage:
    """One bounded, consecutive output-frame range of an alignment."""

    alignment: ElectricalVideoAlignment
    total_frame_count: int
    first_frame: int
    frame_count: int
    next_first_frame: int | None
    presentation_time_seconds: list[float]


def resolve_alignable_provenance(video: VideoFile) -> VideoProvenanceBundle:
    """Verified ancestry with anchors, or a documented refusal."""
    try:
        provenance = resolve_video_provenance(video)
    except ValueError as exc:
        raise ElectricalAlignmentUnavailable(
            ElectricalAlignmentRefusal.PROVENANCE_UNAVAILABLE, str(exc)
        ) from None
    if isinstance(provenance, VideoProvenanceRecovery):
        raise ElectricalAlignmentUnavailable(
            ElectricalAlignmentRefusal.PROVENANCE_UNRESOLVED,
            "Electrical matching is unavailable: frame mapping is unresolved",
        )
    if provenance.synchronization is None:
        raise ElectricalAlignmentUnavailable(
            ElectricalAlignmentRefusal.SYNCHRONIZATION_MISSING,
            "Independent clocks require recording synchronization anchors",
        )
    return provenance


def describe_alignment_eligibility(video: VideoFile) -> ElectricalAlignmentEligibility:
    if not video.processed_video_hash:
        return ElectricalAlignmentEligibility(
            eligible=False,
            refusal=ElectricalAlignmentRefusal.PROVENANCE_UNAVAILABLE,
            message="No processed video generation is published",
            total_frame_count=None,
        )
    try:
        proof = resolve_alignable_provenance(video)
    except ElectricalAlignmentUnavailable as exc:
        return ElectricalAlignmentEligibility(
            eligible=False,
            refusal=exc.code,
            message=exc.message,
            total_frame_count=None,
        )
    return ElectricalAlignmentEligibility(
        eligible=True,
        refusal=None,
        message=None,
        total_frame_count=len(proof.transformations[-1].output.presentation_timestamps),
    )


def load_measurement_series(
    measurement: VideoElectrosurgicalMeasurement,
) -> ElectrosurgicalMeasurementSeries:
    """Decrypt through the storage backend and re-verify every bound identity."""
    # Authenticated decrypted range reads through the storage backend; the
    # plaintext never touches the filesystem and the size is bounded first.
    field_file = measurement.source_file
    size = field_file_size(field_file)
    if not 0 < size <= MAX_ELECTROSURGICAL_SOURCE_BYTES:
        _refuse_integrity(measurement, "stored document size out of bounds")
    payload = b"".join(
        iter_field_file_bytes(field_file, start=0, end=size - 1, chunk_size=1024 * 1024)
    )
    if get_bytes_hash(payload) != measurement.payload_sha256:
        _refuse_integrity(measurement, "stored document hash mismatch")
    try:
        series = validate_series_bytes(payload)
    except ElectrosurgicalImportError:
        _refuse_integrity(measurement, "stored document no longer validates")
    timestamps = series.timestamps
    if (
        series.source_sha256 != measurement.source_sha256
        or timestamps.session_id != measurement.session_id
        or timestamps.clock_id != measurement.electrical_clock_id
        or len(timestamps.presentation_timestamps) != measurement.sample_count
    ):
        _refuse_integrity(measurement, "stored document identity mismatch")
    return series


def _refuse_integrity(
    measurement: VideoElectrosurgicalMeasurement, reason: str
) -> NoReturn:
    emit_structured_event(
        logger,
        "electrosurgical_measurement_integrity",
        level=logging.ERROR,
        status="failed",
        reason=reason,
        video_id=measurement.video_id,
        measurement_id=measurement.pk,
        source_sha256=measurement.source_sha256,
    )
    raise ElectricalAlignmentUnavailable(
        ElectricalAlignmentRefusal.SOURCE_INTEGRITY_FAILED,
        "The stored electrical measurement failed its integrity check",
    )


def align_measurement_frames(
    video: VideoFile,
    measurement: VideoElectrosurgicalMeasurement,
    *,
    expected_generation_content_hash: str,
    first_frame: int,
    frame_count: int,
    channel_ids: tuple[str, ...] = (),
) -> ElectricalAlignmentPage:
    """Align ``[first_frame, first_frame + frame_count)`` of the current generation.

    The range is clipped at the last output frame and reports the next start.
    The final output frame has no recorded exclusive recording end on the host,
    so it is reported as ``unbounded_interval`` instead of being estimated.
    """
    if measurement.video_id != video.pk:
        raise ValueError("Measurement belongs to another video")
    if video.processed_video_hash != expected_generation_content_hash:
        raise ElectricalAlignmentUnavailable(
            ElectricalAlignmentRefusal.STALE_GENERATION,
            "The requested video generation is not the published generation",
        )
    if video.raw_video_hash != measurement.recording_content_hash:
        raise ElectricalAlignmentUnavailable(
            ElectricalAlignmentRefusal.RECORDING_MISMATCH,
            "The measurement was bound to a different recording identity",
        )
    if not 1 <= frame_count <= MAX_ALIGNMENT_FRAMES_PER_REQUEST or first_frame < 0:
        raise ElectricalAlignmentUnavailable(
            ElectricalAlignmentRefusal.INVALID_RANGE,
            f"Frame ranges must start at 0 or later and hold 1 to "
            f"{MAX_ALIGNMENT_FRAMES_PER_REQUEST} frames",
        )
    known_channels = {
        channel.channel_id
        for channel in parse_channel_descriptors(measurement.channels).channels
    }
    unknown = sorted(set(channel_ids) - known_channels)
    if unknown:
        raise ElectricalAlignmentUnavailable(
            ElectricalAlignmentRefusal.UNKNOWN_CHANNEL,
            "Unknown channel identifiers: " + ", ".join(unknown),
        )

    proof = resolve_alignable_provenance(video)
    output = proof.transformations[-1].output
    total = len(output.presentation_timestamps)
    if output.content_hash != expected_generation_content_hash:
        raise ElectricalAlignmentUnavailable(
            ElectricalAlignmentRefusal.STALE_GENERATION,
            "Frame ancestry does not describe the requested generation",
        )
    if first_frame >= total:
        raise ElectricalAlignmentUnavailable(
            ElectricalAlignmentRefusal.INVALID_RANGE,
            "Requested frame range lies outside the video",
        )
    count = min(frame_count, total - first_frame)

    series = load_measurement_series(measurement)
    if channel_ids:
        selected = set(channel_ids)
        series = series.model_copy(
            update={
                "channels": [
                    channel
                    for channel in series.channels
                    if channel.channel_id in selected
                ]
            }
        )
    try:
        alignment = align_electrosurgical_measurements(
            proof,
            series,
            parse_aggregation_profile(measurement.aggregation_profile),
            recording_end_timestamp=None,
            first_output_frame=first_frame,
            output_frame_count=count,
        )
    except ElectricalAlignmentError as exc:
        emit_structured_event(
            logger,
            "electrosurgical_alignment",
            level=logging.WARNING,
            status="rejected",
            reason=str(exc),
            video_id=video.pk,
            measurement_id=measurement.pk,
            generation_content_hash=expected_generation_content_hash,
        )
        raise ElectricalAlignmentUnavailable(
            ElectricalAlignmentRefusal.ALIGNMENT_REJECTED, str(exc)
        ) from None
    if (
        alignment.generation_content_hash != expected_generation_content_hash
        or alignment.recording_content_hash != measurement.recording_content_hash
        or alignment.measurement_source_sha256 != measurement.source_sha256
    ):
        raise RuntimeError("Alignment identity disagrees with its resolved inputs")

    seconds = [
        presentation_timestamp_seconds(
            frame.presentation_timestamp,
            alignment.generation_time_base_num,
            alignment.generation_time_base_den,
        )
        for frame in alignment.frames
    ]
    end = first_frame + count
    emit_structured_event(
        logger,
        "electrosurgical_alignment",
        level=logging.DEBUG,
        status=alignment.status,
        video_id=video.pk,
        measurement_id=measurement.pk,
        generation_content_hash=expected_generation_content_hash,
        first_frame=first_frame,
        frame_count=count,
        algorithm=alignment.algorithm,
    )
    return ElectricalAlignmentPage(
        alignment=alignment,
        total_frame_count=total,
        first_frame=first_frame,
        frame_count=count,
        next_first_frame=end if end < total else None,
        presentation_time_seconds=seconds,
    )


__all__ = [
    "MAX_ALIGNMENT_FRAMES_PER_REQUEST",
    "ElectricalAlignmentEligibility",
    "ElectricalAlignmentPage",
    "ElectricalAlignmentRefusal",
    "ElectricalAlignmentUnavailable",
    "align_measurement_frames",
    "describe_alignment_eligibility",
    "load_measurement_series",
    "resolve_alignable_provenance",
]

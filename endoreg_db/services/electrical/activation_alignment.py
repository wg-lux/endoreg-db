"""Conservative activation coverage on verified processed-frame ancestry.

Summary values remain per activation. Bars cover whole output-frame windows;
they do not claim a reconstructed power curve or sub-frame timing precision.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction

from lx_dtypes.models.contracts.electrical_video_alignment import (
    electrical_to_recording_ticks,
)
from lx_dtypes.models.contracts.electrosurgical_activations import (
    ElectrosurgicalActivation,
)
from lx_dtypes.models.contracts.video_provenance import (
    RecordingSynchronization,
    VideoFrameTimeline,
)

from endoreg_db.models.media.video.electrosurgical_activation import (
    VideoElectrosurgicalActivationLog,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.schemas.electrosurgical_activation import (
    ActivationAlignedPage,
    ActivationFrameCoverage,
    ActivationImportMetadata,
    ActivationMappingStatus,
    ActivationPageRange,
)
from endoreg_db.services.electrical.activation_storage import load_activation_export
from endoreg_db.services.electrical.video_alignment import (
    ElectricalAlignmentRefusal,
    ElectricalAlignmentUnavailable,
    resolve_alignable_provenance,
)
from endoreg_db.services.video_files.segment_coordinates import bundle_frame_ancestry
from endoreg_db.utils.video.timeline import presentation_timestamp_seconds

MAX_ACTIVATIONS_PER_REQUEST = 500
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def activation_frame_coverage(
    activation: ElectrosurgicalActivation,
    *,
    activation_index: int,
    synchronization: RecordingSynchronization,
    recording_starts: list[int],
    output: VideoFrameTimeline,
) -> ActivationFrameCoverage:
    def unmapped(status: ActivationMappingStatus) -> ActivationFrameCoverage:
        return ActivationFrameCoverage(activation_index=activation_index, status=status)

    delta = activation.started_at - _EPOCH
    start_us = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    end_us = start_us + Fraction(activation.duration_s) * 1_000_000
    anchors = synchronization.anchors
    if (
        start_us < anchors[0].electrical_timestamp
        or end_us > anchors[-1].electrical_timestamp
    ):
        return unmapped(ActivationMappingStatus.OUTSIDE_SYNCHRONIZATION)
    start = electrical_to_recording_ticks(synchronization, start_us)
    end = electrical_to_recording_ticks(synchronization, end_us)
    if start < recording_starts[0]:
        return unmapped(ActivationMappingStatus.OUTSIDE_RECORDING)
    if end > recording_starts[-1]:
        return unmapped(ActivationMappingStatus.UNBOUNDED_INTERVAL)
    window = bisect_right(recording_starts, start) - 1
    first = bisect_left(recording_starts, recording_starts[window])
    last = first if activation.duration_s == 0 else bisect_left(recording_starts, end)
    return ActivationFrameCoverage(
        activation_index=activation_index,
        status=ActivationMappingStatus.MAPPED,
        summary=activation,
        first_frame=first,
        end_frame_exclusive=last,
        start_seconds=presentation_timestamp_seconds(
            output.presentation_timestamps[first],
            output.time_base_num,
            output.time_base_den,
        ),
        end_seconds=presentation_timestamp_seconds(
            output.presentation_timestamps[last],
            output.time_base_num,
            output.time_base_den,
        ),
    )


def align_activation_export(
    video: VideoFile,
    record: VideoElectrosurgicalActivationLog,
    *,
    expected_generation_content_hash: str,
    first_activation: int = 0,
    activation_count: int = MAX_ACTIVATIONS_PER_REQUEST,
) -> ActivationAlignedPage:
    if record.video_id != video.pk:
        raise ValueError("Activation log belongs to another video")
    if video.processed_video_hash != expected_generation_content_hash:
        raise ElectricalAlignmentUnavailable(
            ElectricalAlignmentRefusal.STALE_GENERATION, "Video generation changed"
        )
    metadata = ActivationImportMetadata.model_validate(record.metadata)
    if metadata.synchronization.source_content_hash != video.raw_video_hash:
        raise ElectricalAlignmentUnavailable(
            ElectricalAlignmentRefusal.RECORDING_MISMATCH, "Recording identity changed"
        )
    if (
        not 0 <= first_activation < metadata.activation_count
        or not 1 <= activation_count <= MAX_ACTIVATIONS_PER_REQUEST
    ):
        raise ElectricalAlignmentUnavailable(
            ElectricalAlignmentRefusal.INVALID_RANGE, "Invalid activation range"
        )
    proof = resolve_alignable_provenance(video)
    if proof.synchronization != metadata.synchronization:
        raise ElectricalAlignmentUnavailable(
            ElectricalAlignmentRefusal.ALIGNMENT_REJECTED,
            "Recording synchronization changed",
        )
    output = proof.transformations[-1].output
    if output.content_hash != expected_generation_content_hash:
        raise ElectricalAlignmentUnavailable(
            ElectricalAlignmentRefusal.STALE_GENERATION,
            "Provenance describes another generation",
        )
    parsed = load_activation_export(record)
    source = proof.transformations[0].source
    starts = [
        source.presentation_timestamps[index] for index in bundle_frame_ancestry(proof)
    ]
    end = min(first_activation + activation_count, metadata.activation_count)
    sync = metadata.synchronization
    return ActivationAlignedPage(
        video_id=video.pk,
        activation_log_id=record.pk,
        source_sha256=record.source_sha256,
        generation_content_hash=output.content_hash,
        timing_error_seconds=Decimal(sync.uncertainty_num)
        / Decimal(sync.uncertainty_den)
        + metadata.timestamp_rounding_error_seconds,
        range=ActivationPageRange(
            first_activation=first_activation,
            activation_count=end - first_activation,
            total_activation_count=metadata.activation_count,
            next_first_activation=end if end < metadata.activation_count else None,
        ),
        activations=[
            activation_frame_coverage(
                parsed.log.activations[index],
                activation_index=index,
                synchronization=sync,
                recording_starts=starts,
                output=output,
            )
            for index in range(first_activation, end)
        ],
    )

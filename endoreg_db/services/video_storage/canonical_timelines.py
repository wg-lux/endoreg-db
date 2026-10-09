"""Canonical raw/processed timestamp provenance; streaming builds are excluded."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from lx_dtypes.models.contracts.video_file import VideoFileMetaJsonObject

from endoreg_db.schemas.video_storage import (
    CanonicalFrameTimeline,
    CanonicalTimelineHistoryEntry,
    CanonicalTimestampTransformation,
    VideoArtifactProbe,
    VideoStorageNormalizationEvidence,
    SegmentTimelineReference,
)
from endoreg_db.services.video_storage.contracts import (
    VideoStorageNormalizationError,
    VideoStorageProfile,
    MeasuredAverageFrameRateDriftError,
)
from endoreg_db.services.video_storage.probes import probe_video_frame_timestamps
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.services.video_files.types import VideoArtifactKind


class ValidateOutput(Protocol):
    def __call__(
        self,
        *,
        source: VideoArtifactProbe,
        output: VideoArtifactProbe,
        profile: VideoStorageProfile,
        segments: list[SegmentTimelineReference] | None,
        canonical_timestamps: CanonicalTimestampTransformation | None = None,
        artifact_kind: VideoArtifactKind = VideoArtifactKind.RAW,
    ) -> VideoStorageNormalizationEvidence: ...


def validate_canonical_output(
    *,
    source: VideoArtifactProbe,
    output: VideoArtifactProbe,
    before: CanonicalFrameTimeline,
    output_path: Path,
    profile: VideoStorageProfile,
    segments: list[SegmentTimelineReference] | None,
    validate_output: ValidateOutput,
    artifact_kind: VideoArtifactKind = VideoArtifactKind.RAW,
) -> VideoStorageNormalizationEvidence:
    """Validate metadata first; only decoded proof may resolve average-rate drift."""
    if artifact_kind == VideoArtifactKind.PROCESSED:
        from endoreg_db.services.video_storage.validation import (
            assert_storage_compliance,
        )

        assert_storage_compliance(output, profile=profile)
        timestamps = CanonicalTimestampTransformation(
            before=before,
            after=capture_canonical_timeline(output_path, output),
        )
        return validate_output(
            source=source,
            output=output,
            profile=profile,
            segments=segments,
            canonical_timestamps=timestamps,
            artifact_kind=artifact_kind,
        )
    try:
        evidence = validate_output(
            source=source, output=output, profile=profile, segments=segments
        )
    except MeasuredAverageFrameRateDriftError:
        timestamps = CanonicalTimestampTransformation(
            before=before,
            after=capture_canonical_timeline(output_path, output),
        )
        return validate_output(
            source=source,
            output=output,
            profile=profile,
            segments=segments,
            canonical_timestamps=timestamps,
        )
    return with_canonical_timestamps(evidence, before, output_path)


def capture_canonical_timeline(
    path: Path, probe: VideoArtifactProbe, *, require_original_pts: bool = False
) -> CanonicalFrameTimeline:
    """Read actual decoded coordinates, never estimates derived from frame rate."""
    timeline = probe.timeline
    if timeline.time_base_num is None or timeline.time_base_den is None:
        raise VideoStorageNormalizationError(
            "Canonical frame timestamps require a stream time base"
        )
    identity = path.stat()
    frames = (
        probe_video_frame_timestamps(path, require_original_pts=True)
        if require_original_pts
        else probe_video_frame_timestamps(path)
    )
    result = CanonicalFrameTimeline(
        content_hash=get_file_hash(path),
        time_base_num=timeline.time_base_num,
        time_base_den=timeline.time_base_den,
        presentation_timestamps=[frame.presentation_timestamp for frame in frames],
    )
    current = path.stat()
    if (
        identity.st_dev,
        identity.st_ino,
        identity.st_size,
        identity.st_mtime_ns,
        identity.st_ctime_ns,
    ) != (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
        current.st_ctime_ns,
    ):
        raise VideoStorageNormalizationError(
            "Canonical source changed during timestamp capture"
        )
    return result


def assert_canonical_timeline_equivalence(
    before: CanonicalFrameTimeline, after: CanonicalFrameTimeline
) -> None:
    """Require the same frame identities and timestamps, allowing muxer rounding only."""
    if len(before.presentation_timestamps) != len(after.presentation_timestamps):
        raise VideoStorageNormalizationError("Canonical decoded frame count changed")
    # Compare integer ticks on a common denominator without rounding long timelines.
    source_scale = before.time_base_num * after.time_base_den
    output_scale = after.time_base_num * before.time_base_den
    for index, (source_pts, output_pts) in enumerate(
        zip(before.presentation_timestamps, after.presentation_timestamps, strict=True)
    ):
        if (
            abs(source_pts * source_scale - output_pts * output_scale) * 2
            > output_scale
        ):
            raise VideoStorageNormalizationError(
                f"Canonical presentation timestamp changed at frame {index}"
            )


def with_canonical_timestamps(
    evidence: VideoStorageNormalizationEvidence,
    before: CanonicalFrameTimeline,
    output_path: Path,
    *,
    preserve_timestamps: bool = True,
) -> VideoStorageNormalizationEvidence:
    transformation = CanonicalTimestampTransformation(
        before=before,
        after=capture_canonical_timeline(output_path, evidence.output),
    )
    if preserve_timestamps:
        assert_canonical_timeline_equivalence(
            transformation.before, transformation.after
        )
    return evidence.model_copy(update={"canonical_timestamps": transformation})


def append_canonical_timeline_history(
    meta: VideoFileMetaJsonObject | None,
    evidence: VideoStorageNormalizationEvidence,
    *,
    artifact_kind: Literal["raw", "processed"],
    output_content_hash: str,
) -> VideoFileMetaJsonObject:
    """Return publication metadata retaining every earlier canonical transformation."""
    timestamps = evidence.canonical_timestamps
    if timestamps is None:
        raise VideoStorageNormalizationError(
            "Canonical publication requires before/after frame timestamps"
        )
    if timestamps.after.content_hash != output_content_hash:
        raise VideoStorageNormalizationError(
            "Canonical timestamp evidence does not match the published content"
        )
    result: VideoFileMetaJsonObject = dict(meta or {})
    previous = result.get("canonical_timeline_history", [])
    if not isinstance(previous, list):
        raise VideoStorageNormalizationError(
            "Canonical timestamp history must be a list"
        )
    history = [
        CanonicalTimelineHistoryEntry.model_validate(entry) for entry in previous
    ]
    entry = CanonicalTimelineHistoryEntry(
        artifact_kind=artifact_kind,
        before=timestamps.before,
        after=timestamps.after,
        segment_mappings=evidence.segment_mappings,
    )
    if not history or history[-1] != entry:
        history.append(entry)
    result["canonical_timeline_history"] = [
        item.model_dump(mode="json") for item in history
    ]
    return result

"""Canonical raw/processed timestamp provenance; streaming builds are excluded."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from lx_dtypes.models.contracts.video_file import VideoFileMetaJsonObject

from endoreg_db.schemas.video_storage import (
    CanonicalFrameTimeline,
    CanonicalTimelineHistoryEntry,
    CanonicalTimestampTransformation,
    VideoArtifactProbe,
    VideoStorageNormalizationEvidence,
)
from endoreg_db.services.video_storage.contracts import VideoStorageNormalizationError
from endoreg_db.services.video_storage.probes import probe_video_frame_timestamps
from endoreg_db.utils.hashs import get_file_hash


def capture_canonical_timeline(
    path: Path, probe: VideoArtifactProbe
) -> CanonicalFrameTimeline:
    """Read actual decoded coordinates, never estimates derived from frame rate."""
    timeline = probe.timeline
    if timeline.time_base_num is None or timeline.time_base_den is None:
        raise VideoStorageNormalizationError(
            "Canonical frame timestamps require a stream time base"
        )
    identity = path.stat()
    frames = probe_video_frame_timestamps(path)
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


def with_canonical_timestamps(
    evidence: VideoStorageNormalizationEvidence,
    before: CanonicalFrameTimeline,
    output_path: Path,
) -> VideoStorageNormalizationEvidence:
    transformation = CanonicalTimestampTransformation(
        before=before,
        after=capture_canonical_timeline(output_path, evidence.output),
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
        artifact_kind=artifact_kind, before=timestamps.before, after=timestamps.after
    )
    if not history or history[-1] != entry:
        history.append(entry)
    result["canonical_timeline_history"] = [
        item.model_dump(mode="json") for item in history
    ]
    return result

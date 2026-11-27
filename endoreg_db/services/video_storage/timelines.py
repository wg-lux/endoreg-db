from __future__ import annotations

import math
import logging
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from collections.abc import Callable

from django.db import transaction
from lx_dtypes.models.contracts.video_file import VideoFileMetaJsonObject

from endoreg_db.schemas.video_storage import (
    CanonicalFrameTimeline,
    CanonicalTimelineHistoryEntry,
    FramePresentationTimestamp,
    SegmentTimelineReference,
    VideoArtifactProbe,
    VideoSourceTimelineEvidence,
    VideoTimelineContract,
)
from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.structured_logging import emit_structured_event
from endoreg_db.services.video_files.types import VideoArtifactKind
from endoreg_db.services.video_storage.contracts import (
    VideoStorageNormalizationError,
    evidence_as_json,
)
from endoreg_db.utils.video.timeline import (
    VideoTimelineMapping,
    VideoTimelineMappingError,
    frame_number_to_seconds,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.frame.frame import Frame
    from endoreg_db.models.media.video.video_file import VideoFile

TimestampMapping = Literal["ffprobe_pts", "rational_cfr"]
logger = logging.getLogger(__name__)


def timeline_from_video_metadata(
    *, fps: float, duration_seconds: float, frame_count: int
) -> VideoTimelineContract:
    if not math.isfinite(fps) or fps <= 0:
        raise VideoStorageNormalizationError("Stored video FPS must be positive")
    frame_rate = Fraction(str(fps)).limit_denominator(1_000_000)
    return VideoTimelineContract(
        fps_num=frame_rate.numerator,
        fps_den=frame_rate.denominator,
        duration_seconds=duration_seconds,
        frame_count=frame_count,
    )


def segment_timeline_references(
    video: object,
    *,
    timeline: VideoTimelineContract,
) -> list[SegmentTimelineReference]:
    related = getattr(video, "label_video_segments", None)
    if related is None:
        return []
    rows = related.order_by("pk").values_list(
        "pk",
        "start_frame_number",
        "end_frame_number",
    )
    segment_rows = list(rows)
    if not segment_rows:
        return []
    frame_numbers = {
        int(frame_number)
        for _, start_frame, end_frame in segment_rows
        for frame_number in (start_frame, end_frame)
    }
    persisted: dict[int, float] = {}
    frames = getattr(video, "frames", None)
    if frames is not None:
        persisted = {
            int(frame_number): float(timestamp)
            for frame_number, timestamp in frames.filter(
                frame_number__in=frame_numbers | {0},
                timestamp__isnull=False,
            ).values_list("frame_number", "timestamp")
        }
    required_persisted_frames = frame_numbers - {timeline.frame_count}
    if timeline.variable_frame_rate and required_persisted_frames - persisted.keys():
        missing = sorted(required_persisted_frames - persisted.keys())
        raise VideoStorageNormalizationError(
            "Variable-frame-rate segment mapping requires persisted PTS for "
            f"every boundary; missing frames: {missing}"
        )
    mapping = _mapping_from_contract(timeline)
    first_timestamp = persisted.get(0)
    if first_timestamp is not None:
        from dataclasses import replace

        mapping = replace(mapping, first_timestamp_seconds=float(first_timestamp))
    references: list[SegmentTimelineReference] = []
    for segment_id, start_frame, end_frame in segment_rows:
        start_number = int(start_frame)
        end_number = int(end_frame)
        try:
            references.append(
                SegmentTimelineReference(
                    segment_id=int(segment_id),
                    origin_timestamp_seconds=mapping.first_timestamp_seconds,
                    start_frame=start_number,
                    end_frame=end_number,
                    start_timestamp_seconds=frame_number_to_seconds(
                        mapping,
                        start_number,
                        persisted_timestamp=persisted.get(start_number),
                    ),
                    end_timestamp_seconds=frame_number_to_seconds(
                        mapping,
                        end_number,
                        persisted_timestamp=persisted.get(end_number),
                    ),
                )
            )
        except VideoTimelineMappingError as exc:
            raise VideoStorageNormalizationError(str(exc)) from exc
    return references


def persist_video_source_timeline(
    video: "VideoFile",
    path: Path,
    *,
    probe_artifact: Callable[[Path], VideoArtifactProbe],
    probe_frame_pts: Callable[[Path], list[float]],
    probe_frame_timestamps: Callable[[Path], list[FramePresentationTimestamp]]
    | None = None,
    artifact_kind: VideoArtifactKind = VideoArtifactKind.RAW,
) -> None:
    """Persist a versioned source timeline and per-frame presentation times."""
    from endoreg_db.models.label.annotation.video_segmentation_annotation import (
        VideoSegmentationAnnotation,
    )
    from endoreg_db.models.media.frame.frame import Frame
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.services.video_files.frames import initialize_video_frames

    identity = path.stat()
    source = probe_artifact(path)
    exact_timestamps = (
        probe_frame_timestamps(path) if probe_frame_timestamps is not None else None
    )
    captured = None
    exact_seconds: list[float] | None = None
    if exact_timestamps is not None:
        exact_seconds = _exact_timestamp_seconds(source, exact_timestamps)
        assert source.timeline.time_base_num is not None
        assert source.timeline.time_base_den is not None
        captured = CanonicalFrameTimeline(
            content_hash=get_file_hash(path),
            time_base_num=source.timeline.time_base_num,
            time_base_den=source.timeline.time_base_den,
            presentation_timestamps=[
                row.presentation_timestamp for row in exact_timestamps
            ],
        )
    after_capture = path.stat()
    if any(
        getattr(identity, name) != getattr(after_capture, name)
        for name in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    ):
        raise VideoStorageNormalizationError(
            "Canonical source changed during timestamp capture"
        )
    with transaction.atomic():
        current = VideoFile.objects.select_for_update().get(pk=video.pk)
        video.meta = current.meta
        video.frame_count = current.frame_count
        payload = VideoFileMetaPayload.model_validate(current.meta or {})
        meta: VideoFileMetaJsonObject = dict(current.meta or {})
        if captured is not None:
            history = list(payload.canonical_timeline_history or [])
            candidates = [
                timeline
                for entry in history
                for timeline in (entry.before, entry.after)
                if timeline.content_hash == captured.content_hash
            ]
            if any(timeline != captured for timeline in candidates):
                raise VideoStorageNormalizationError(
                    "Decoded timestamps conflict with canonical history"
                )
            if not any(
                entry.artifact_kind == artifact_kind.value and entry.after == captured
                for entry in history
            ):
                history.append(
                    CanonicalTimelineHistoryEntry(
                        artifact_kind=artifact_kind.value,
                        before=captured,
                        after=captured,
                    )
                )
            meta["canonical_timeline_history"] = [
                entry.model_dump(mode="json") for entry in history
            ]
        # Reimport observes raw evidence without moving the processed annotation
        # generation back onto the recording's ordinals.
        if (
            artifact_kind == VideoArtifactKind.RAW
            and current.processed_file
            and (
                captured is None
                or current.processed_video_hash != captured.content_hash
            )
        ):
            if captured is None:
                raise VideoStorageNormalizationError(
                    "Raw history recovery requires exact timestamps"
                )
            current.meta = meta
            current.save(update_fields=["meta", "date_modified"])
            video.meta = current.meta
            return
        rows = list(
            Frame.objects.select_for_update()
            .filter(video=video)
            .order_by("frame_number")
        )
        if not rows and not exact_timestamps:
            raise VideoStorageNormalizationError(
                "Source timeline initialization requires decoded presentation timestamps"
            )
        source, timestamps, mapping = _resolve_source_timestamps(
            video=video,
            path=path,
            source=source,
            rows=rows,
            exact_seconds=exact_seconds,
            probe_frame_pts=probe_frame_pts,
        )
        repair_unpublished = (
            exact_timestamps is not None
            and artifact_kind == VideoArtifactKind.RAW
            and not current.processed_file
            and (
                payload.source_timeline is None
                or (
                    payload.source_timeline.timestamp_mapping == "rational_cfr"
                    and payload.source_timeline.artifact_kind in (None, "raw")
                    and payload.source_timeline.content_hash
                    in (None, captured.content_hash if captured else None)
                )
            )
            and not payload.canonical_timeline_history
            and not current.label_video_segments.exists()
            and not any(row.is_extracted for row in rows)
            and not Frame.objects.filter(
                video=current, box_annotations__isnull=False
            ).exists()
            and not Frame.objects.filter(
                video=current, image_classification_annotations__isnull=False
            ).exists()
            and not VideoSegmentationAnnotation.objects.filter(
                video_file=current
            ).exists()
        )
        # Older imports created estimated placeholders before decoding. Only
        # extend a pristine prefix; never delete or renumber persisted frames.
        extend_placeholders = (
            bool(rows)
            and repair_unpublished
            and len(rows) < len(timestamps)
            and all(
                row.frame_number == index
                and row.timestamp is None
                and row.presentation_timestamp is None
                for index, row in enumerate(rows)
            )
        )
        if not rows or extend_placeholders:
            if video.label_video_segments.exists():
                raise VideoStorageNormalizationError(
                    "Cannot initialize a source timeline after segments were persisted"
                )
            video.frame_count = len(timestamps)
            initialize_video_frames(video)
            rows = list(Frame.objects.filter(video=video).order_by("frame_number"))
        if len(timestamps) != len(rows):
            raise VideoStorageNormalizationError(
                "Probed presentation timestamp count does not match persisted frames"
            )
        video.frame_count = len(timestamps)
        source = _source_with_probed_frame_count(source, len(timestamps))
        update_fields = _apply_frame_timestamps(
            rows, timestamps, exact_timestamps, repair_unpublished=repair_unpublished
        )
        Frame.objects.bulk_update(rows, update_fields, batch_size=2000)
        evidence = VideoSourceTimelineEvidence(
            persisted_at=datetime.now(UTC),
            source=source,
            timestamp_mapping=mapping,
            artifact_kind=artifact_kind.value,
            content_hash=captured.content_hash if captured is not None else None,
            first_timestamp_seconds=timestamps[0],
            end_timestamp_seconds=timestamps[0] + source.timeline.duration_seconds,
        )
        meta["source_timeline"] = evidence_as_json(evidence)
        video.meta = meta
        video.save(update_fields=["meta", "frame_count", "date_modified"])
        if repair_unpublished:
            emit_structured_event(
                logger,
                "unpublished_raw_timestamps_reconciled",
                video_id=video.pk,
                frame_count=len(rows),
            )


def _resolve_source_timestamps(
    *,
    video: "VideoFile",
    path: Path,
    source: VideoArtifactProbe,
    rows: list["Frame"],
    exact_seconds: list[float] | None,
    probe_frame_pts: Callable[[Path], list[float]],
) -> tuple[VideoArtifactProbe, list[float], TimestampMapping]:
    if exact_seconds is not None:
        return source, exact_seconds, "ffprobe_pts"
    if source.timeline.variable_frame_rate:
        return source, probe_frame_pts(path), "ffprobe_pts"
    return _constant_frame_rate_timestamps(video, source, rows)


def _exact_timestamp_seconds(
    source: VideoArtifactProbe,
    exact_timestamps: list[FramePresentationTimestamp],
) -> list[float]:
    time_base_num = source.timeline.time_base_num
    time_base_den = source.timeline.time_base_den
    if time_base_num is None or time_base_den is None:
        raise VideoStorageNormalizationError(
            "Exact presentation timestamps require a stream time base"
        )
    tick_seconds = time_base_num / time_base_den
    for index, item in enumerate(exact_timestamps):
        expected_seconds = item.presentation_timestamp * tick_seconds
        if not math.isclose(
            item.presentation_time_seconds,
            expected_seconds,
            rel_tol=0.0,
            abs_tol=max(1e-9, tick_seconds / 2),
        ):
            raise VideoStorageNormalizationError(
                "Frame presentation timestamp does not match stream time base: "
                f"frame={index}"
            )
    return [item.presentation_time_seconds for item in exact_timestamps]


def _constant_frame_rate_timestamps(
    video: "VideoFile",
    source: VideoArtifactProbe,
    rows: list["Frame"],
) -> tuple[VideoArtifactProbe, list[float], TimestampMapping]:
    if video.fps is None or video.duration is None or video.frame_count is None:
        raise VideoStorageNormalizationError(
            "CFR timeline persistence requires FPS, duration, and frame count"
        )
    persisted_timeline = timeline_from_video_metadata(
        fps=float(video.fps),
        duration_seconds=float(video.duration),
        frame_count=int(video.frame_count),
    )
    if len(rows) != persisted_timeline.frame_count:
        raise VideoStorageNormalizationError(
            "Frame row count does not match persisted CFR metadata"
        )
    source = source.model_copy(update={"timeline": persisted_timeline})
    mapping = _mapping_from_contract(persisted_timeline)
    timestamps = [
        frame_number_to_seconds(
            mapping,
            int(row.frame_number),
            persisted_timestamp=None,
        )
        for row in rows
    ]
    return source, timestamps, "rational_cfr"


def _source_with_probed_frame_count(
    source: VideoArtifactProbe,
    frame_count: int,
) -> VideoArtifactProbe:
    timeline = source.timeline.model_copy(update={"frame_count": frame_count})
    return source.model_copy(update={"timeline": timeline})


def _apply_frame_timestamps(
    rows: list["Frame"],
    timestamps: list[float],
    exact_timestamps: list[FramePresentationTimestamp] | None,
    *,
    repair_unpublished: bool = False,
) -> list[str]:
    for index, (row, timestamp) in enumerate(zip(rows, timestamps, strict=True)):
        if (
            row.frame_number != index
            or (
                not repair_unpublished
                and row.timestamp is not None
                and not math.isclose(
                    row.timestamp, timestamp, rel_tol=0.0, abs_tol=1e-6
                )
            )
            or (
                not repair_unpublished
                and exact_timestamps is not None
                and row.presentation_timestamp is not None
                and row.presentation_timestamp
                != exact_timestamps[index].presentation_timestamp
            )
        ):
            raise VideoStorageNormalizationError(
                "Existing frame coordinates conflict with decoded raw media"
            )
        if row.timestamp is None or repair_unpublished:
            row.timestamp = timestamp
        if exact_timestamps is not None and (
            row.presentation_timestamp is None or repair_unpublished
        ):
            row.presentation_timestamp = exact_timestamps[index].presentation_timestamp
    if exact_timestamps is None:
        return ["timestamp"]
    return ["timestamp", "presentation_timestamp"]


def _mapping_from_contract(timeline: VideoTimelineContract) -> VideoTimelineMapping:
    return VideoTimelineMapping(
        fps=timeline.fps,
        frame_count=timeline.frame_count,
        duration_seconds=timeline.duration_seconds,
        requires_persisted_pts=timeline.variable_frame_rate,
    )

"""Operator backfill of absent presentation timelines across stored videos.

Classification is read-only. Apply delegates every write to
``backfill_missing_video_timestamps`` so the hash, generation and row-lock
guarantees of the automatic recovery path stay the only way coordinates are
created. Videos without verifiable canonical media are reported, never guessed.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from django.db.models import Q
from pydantic import ValidationError

from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.utils.video.timeline import VideoTimelineMappingError
from endoreg_db.services.video_files.timestamp_backfill import (
    backfill_missing_video_timestamps,
    has_reconciled_video_timeline,
)
from endoreg_db.utils.structured_logging import emit_structured_event

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)


class VideoTimelineBackfillStatus(StrEnum):
    READY = "ready"
    CANDIDATE = "candidate"
    BACKFILLED = "backfilled"
    BLOCKED = "blocked"
    FAILED = "failed"


class VideoTimelineBackfillBlocker(StrEnum):
    INVALID_META = "invalid_meta"
    INTEGRITY_LOST = "integrity_lost"
    STALE_SOURCE_TIMELINE = "stale_source_timeline"
    CANONICAL_MEDIA_MISSING = "canonical_media_missing"
    FRAME_TIMESTAMPS_WITHOUT_SOURCE_TIMELINE = (
        "frame_timestamps_without_source_timeline"
    )
    SEGMENTS_WITHOUT_FRAME_ROWS = "segments_without_frame_rows"


@dataclass(frozen=True, slots=True)
class VideoTimelineBackfillEntry:
    video_id: int
    status: VideoTimelineBackfillStatus
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "video_id": self.video_id,
            "status": self.status.value,
            "reason": self.reason,
        }


@dataclass(slots=True)
class VideoTimelineBackfillResult:
    apply: bool
    entries: list[VideoTimelineBackfillEntry] = field(
        default_factory=list[VideoTimelineBackfillEntry]
    )

    @property
    def failed(self) -> int:
        return sum(
            entry.status == VideoTimelineBackfillStatus.FAILED for entry in self.entries
        )

    def to_dict(self) -> dict[str, object]:
        counts = Counter(entry.status.value for entry in self.entries)
        return {
            "apply": self.apply,
            "counts": {
                status.value: counts[status.value]
                for status in VideoTimelineBackfillStatus
            },
            # Ready videos need no attention; list only what an operator must review.
            "videos": [
                entry.to_dict()
                for entry in self.entries
                if entry.status != VideoTimelineBackfillStatus.READY
            ],
        }


def _has_canonical_media(video: VideoFile) -> bool:
    """Mirror the backfill's artifact choice: processed media wins over raw."""
    processed = bool(video.processed_file)
    media = video.processed_file if processed else video.raw_file
    digest = video.processed_video_hash if processed else video.raw_video_hash
    return bool(
        media and digest and media.name and media.storage.exists(str(media.name))
    )


def classify_video_timeline(
    video: VideoFile,
) -> tuple[VideoTimelineBackfillStatus, VideoTimelineBackfillBlocker | None]:
    """Decide without writing whether a video's timeline is usable or recoverable."""
    try:
        payload = VideoFileMetaPayload.model_validate(video.meta or {})
    except ValidationError:
        return (
            VideoTimelineBackfillStatus.BLOCKED,
            VideoTimelineBackfillBlocker.INVALID_META,
        )
    if payload.integrity_status == "lost":
        return (
            VideoTimelineBackfillStatus.BLOCKED,
            VideoTimelineBackfillBlocker.INTEGRITY_LOST,
        )
    source = payload.source_timeline
    if (
        source is not None
        and source.content_hash is not None
        and source.artifact_kind == "processed"
    ):
        expected = video.processed_video_hash
        if source.content_hash != expected:
            return (
                VideoTimelineBackfillStatus.BLOCKED,
                VideoTimelineBackfillBlocker.STALE_SOURCE_TIMELINE,
            )
    present = Q(timestamp__isnull=False) | Q(presentation_timestamp__isnull=False)
    if video.frames.filter(present).exists():
        if source is None and not _has_canonical_media(video):
            # Existing coordinates are authoritative; recovery never rewrites them.
            return (
                VideoTimelineBackfillStatus.BLOCKED,
                VideoTimelineBackfillBlocker.FRAME_TIMESTAMPS_WITHOUT_SOURCE_TIMELINE,
            )
        if has_reconciled_video_timeline(video, payload):
            return VideoTimelineBackfillStatus.READY, None
    if (
        source is not None
        and source.timestamp_mapping == "rational_cfr"
        and not video.processed_file
    ):
        return VideoTimelineBackfillStatus.READY, None
    if not _has_canonical_media(video):
        return (
            VideoTimelineBackfillStatus.BLOCKED,
            VideoTimelineBackfillBlocker.CANONICAL_MEDIA_MISSING,
        )
    if not video.frames.exists() and video.label_video_segments.exists():
        return (
            VideoTimelineBackfillStatus.BLOCKED,
            VideoTimelineBackfillBlocker.SEGMENTS_WITHOUT_FRAME_ROWS,
        )
    return VideoTimelineBackfillStatus.CANDIDATE, None


def _apply_backfill(
    video: VideoFile,
    *,
    processed_ordinals_operator: str | None = None,
    dry_run: bool = False,
    remap_annotations: bool = False,
) -> VideoTimelineBackfillEntry:
    video_id = int(video.pk)
    try:
        backfill_missing_video_timestamps(
            video,
            processed_ordinals_operator=processed_ordinals_operator,
            dry_run=dry_run,
            remap_annotations=remap_annotations,
        )
    except VideoTimelineMappingError as exc:
        reason = str(exc)
    except Exception as exc:  # noqa: BLE001 - one video must not stop the batch
        # The type alone is reported; messages may carry storage paths.
        reason = type(exc).__name__
    else:
        if dry_run:
            return VideoTimelineBackfillEntry(
                video_id, VideoTimelineBackfillStatus.CANDIDATE
            )
        video.refresh_from_db()
        status, blocker = classify_video_timeline(video)
        if status == VideoTimelineBackfillStatus.READY:
            emit_structured_event(
                logger, "video_timeline_backfill_completed", video_id=video_id
            )
            return VideoTimelineBackfillEntry(
                video_id, VideoTimelineBackfillStatus.BACKFILLED
            )
        reason = f"timeline still unusable: {blocker or status}"
    emit_structured_event(
        logger,
        "video_timeline_backfill_failed",
        level=logging.ERROR,
        video_id=video_id,
        reason=reason,
    )
    return VideoTimelineBackfillEntry(
        video_id, VideoTimelineBackfillStatus.FAILED, reason
    )


def backfill_video_timelines(
    *,
    apply: bool,
    video_ids: Iterable[int] | None = None,
    processed_ordinals_operator: str | None = None,
    remap_annotations: bool = False,
) -> VideoTimelineBackfillResult:
    """Classify every selected video and, with ``apply``, recover candidates."""
    from endoreg_db.models.media.video.video_file import VideoFile

    selected_ids = list(video_ids) if video_ids is not None else None
    if remap_annotations and processed_ordinals_operator is None:
        raise ValueError(
            "Annotation remapping requires explicit processed ordinal confirmation"
        )
    if processed_ordinals_operator is not None and not selected_ids:
        raise ValueError("Processed ordinal recovery requires explicit video IDs")
    queryset = VideoFile.objects.order_by("pk")
    if selected_ids is not None:
        queryset = queryset.filter(pk__in=selected_ids)
        if processed_ordinals_operator is not None and queryset.count() != len(
            set(selected_ids)
        ):
            raise ValueError("A selected video does not exist")
    result = VideoTimelineBackfillResult(apply=apply)
    for video in queryset.iterator(chunk_size=200):
        status, blocker = classify_video_timeline(video)
        if (
            status == VideoTimelineBackfillStatus.CANDIDATE
            or (remap_annotations and status == VideoTimelineBackfillStatus.READY)
            or (
                processed_ordinals_operator is not None
                and blocker == VideoTimelineBackfillBlocker.SEGMENTS_WITHOUT_FRAME_ROWS
            )
        ) and (apply or processed_ordinals_operator is not None):
            result.entries.append(
                _apply_backfill(
                    video,
                    processed_ordinals_operator=processed_ordinals_operator,
                    dry_run=not apply,
                    remap_annotations=remap_annotations,
                )
            )
            continue
        result.entries.append(
            VideoTimelineBackfillEntry(
                int(video.pk), status, blocker.value if blocker else None
            )
        )
    return result

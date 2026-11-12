"""Recover absent presentation timestamps from the unchanged canonical media."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import Q

from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.schemas.video_storage import (
    CanonicalFrameTimeline,
    CanonicalTimelineHistoryEntry,
    VideoArtifactProbe,
    VideoSourceTimelineEvidence,
)
from endoreg_db.services.media.operation_gate import create_video_stream_lease
from endoreg_db.services.video_files.timeline import VideoTimelineMappingError
from endoreg_db.services.video_files.types import VideoArtifactKind
from endoreg_db.services.video_storage.canonical_timelines import (
    capture_canonical_timeline,
)
from endoreg_db.services.video_storage.probes import probe_video_artifact
from endoreg_db.services.video_storage.processed_timelines import frame_boundaries
from endoreg_db.utils.storage import ensure_local_file
from endoreg_db.utils.structured_logging import emit_structured_event

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)


def _capture_generation(
    video: VideoFile,
    kind: VideoArtifactKind,
    payload: VideoFileMetaPayload,
    history: list[CanonicalTimelineHistoryEntry],
) -> tuple[VideoArtifactProbe, CanonicalFrameTimeline]:
    field = video.raw_file if kind == VideoArtifactKind.RAW else video.processed_file
    digest = (
        video.processed_video_hash
        if kind == VideoArtifactKind.PROCESSED
        else video.raw_video_hash
    )
    if not field or not digest:
        raise VideoTimelineMappingError(
            "Timestamp backfill requires canonical media and its content hash."
        )
    with ensure_local_file(field) as path:
        probe = probe_video_artifact(path)
        captured = capture_canonical_timeline(
            path, probe, require_original_pts=True, imported_content_hash=digest
        )
    candidates = [
        timeline
        for entry in history
        for timeline in (entry.before, entry.after)
        if timeline.content_hash == digest
    ]
    if payload.video_frame_provenance is not None:
        candidates.extend(
            timeline
            for entry in payload.video_frame_provenance.transformations
            for timeline in (entry.source, entry.output)
            if timeline.content_hash == digest
        )
    if payload.video_provenance_recovery is not None:
        candidates.extend(
            timeline
            for timeline in (
                payload.video_provenance_recovery.original_timeline,
                payload.video_provenance_recovery.processed_timeline,
            )
            if timeline is not None and timeline.content_hash == digest
        )
    if any(timeline != captured for timeline in candidates):
        raise VideoTimelineMappingError(
            "Decoded timestamps conflict with canonical history."
        )
    if not any(
        entry.artifact_kind == kind.value and entry.after == captured
        for entry in history
    ):
        history.append(
            CanonicalTimelineHistoryEntry(
                artifact_kind=kind.value, before=captured, after=captured
            )
        )
    return probe, captured


def backfill_missing_video_timestamps(
    video: VideoFile, *, payload: VideoFileMetaPayload | None = None
) -> None:
    """Fill only wholly absent coordinates, serialized with generation publication.

    Partial coordinates remain authoritative and are never repaired by guessing.
    No import state, annotation boundary or transformation ancestry is created.
    """
    from endoreg_db.models.media.frame.frame import Frame
    from endoreg_db.models.media.video.video_file import VideoFile

    if not video.pk:
        return
    if payload is None:
        payload = VideoFileMetaPayload.model_validate(video.meta or {})
    existing = payload.source_timeline
    present = Q(timestamp__isnull=False) | Q(presentation_timestamp__isnull=False)
    if video.frames.filter(present).exists():
        if existing is None:
            current = VideoFile.objects.only("meta", "frame_count").get(pk=video.pk)
            if current.meta != video.meta:
                video.meta = current.meta
                video.frame_count = current.frame_count
        return
    with transaction.atomic():
        current = VideoFile.objects.select_for_update().get(pk=video.pk)
        if (current.processed_video_hash, current.raw_video_hash) != (
            video.processed_video_hash,
            video.raw_video_hash,
        ):
            raise VideoTimelineMappingError(
                "Canonical generation changed before timestamp backfill."
            )
        rows = list(current.frames.select_for_update().order_by("frame_number"))
        if any(
            row.timestamp is not None or row.presentation_timestamp is not None
            for row in rows
        ):
            video.meta = current.meta
            video.frame_count = current.frame_count
            return
        payload = VideoFileMetaPayload.model_validate(current.meta or {})
        if payload.integrity_status == "lost":
            raise VideoTimelineMappingError(
                "Cannot backfill timestamps for lost media."
            )
        processed = bool(current.processed_file)
        kind = VideoArtifactKind.PROCESSED if processed else VideoArtifactKind.RAW
        source = payload.source_timeline
        if (
            source is not None
            and source.artifact_kind == "processed"
            and (source.content_hash not in (None, current.processed_video_hash))
        ):
            raise VideoTimelineMappingError(
                "Stored timeline belongs to another canonical generation."
            )
        # Admission rejects active writers. The row lock fences publication and
        # serializes concurrent fallback requests throughout capture and commit.
        create_video_stream_lease(current, file_type=kind)
        history = list(payload.canonical_timeline_history or [])
        # Recover the active generation directly. Retained raw media is not a
        # prerequisite for processed coordinates and is not probed here.
        probe, captured = _capture_generation(current, kind, payload, history)
        boundaries = frame_boundaries(captured, probe)
        count = len(boundaries) - 1
        by_number = {row.frame_number: row for row in rows}
        changed_numbers = set(by_number) != set(range(count))
        if changed_numbers and (
            current.label_video_segments.exists()
            or current.frames.filter(
                Q(is_extracted=True)
                | Q(image_classification_annotations__isnull=False)
                | Q(box_annotations__isnull=False)
            ).exists()
        ):
            raise VideoTimelineMappingError(
                "Existing annotations or extracted frames require timestamp remapping before recovery."
            )
        obsolete = [row.pk for row in rows if row.frame_number >= count]
        if obsolete:
            Frame.objects.filter(pk__in=obsolete).delete()
        rows = [
            by_number.get(index) or Frame(video=current, frame_number=index)
            for index in range(count)
        ]
        for index, row in enumerate(rows):
            row.timestamp = boundaries[index]
            row.presentation_timestamp = captured.presentation_timestamps[index]
        existing_rows = [row for row in rows if row.pk is not None]
        Frame.objects.bulk_create(
            [row for row in rows if row.pk is None], batch_size=2000
        )
        Frame.objects.bulk_update(
            existing_rows, ["timestamp", "presentation_timestamp"], batch_size=2000
        )
        probe = probe.model_copy(
            update={
                "timeline": probe.timeline.model_copy(update={"frame_count": count})
            }
        )
        current.meta = {
            **(current.meta or {}),
            "canonical_timeline_history": [
                entry.model_dump(mode="json") for entry in history
            ],
            "source_timeline": VideoSourceTimelineEvidence(
                persisted_at=datetime.now(UTC),
                source=probe,
                artifact_kind=kind.value,
                content_hash=captured.content_hash,
                timestamp_mapping="ffprobe_pts",
                first_timestamp_seconds=boundaries[0],
                end_timestamp_seconds=boundaries[-1],
            ).model_dump(mode="json"),
        }
        current.frame_count = count
        current.save(update_fields=["meta", "frame_count", "date_modified"])
        video.meta = current.meta
        video.frame_count = count
        emit_structured_event(
            logger,
            "video_timestamps_backfilled",
            video_id=current.pk,
            artifact_kind=kind,
            frame_count=count,
        )

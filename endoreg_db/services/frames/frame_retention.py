from __future__ import annotations

import logging
from typing import NamedTuple

from django.db import transaction
from django.db.models import Q

from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.utils.file_operations import safe_unlink_file

logger = logging.getLogger(__name__)

DEFAULT_UNUSED_FRAME_PRUNE_BATCH_SIZE = 250
OUTSIDE_LABEL_NAME = "outside"


class _SegmentInterval(NamedTuple):
    """Half-open frame interval ``[start_frame, end_frame)`` of one segment."""

    start_frame: int
    end_frame: int
    is_validated_outside: bool


def _segment_intervals(video: VideoFile) -> list[_SegmentInterval]:
    rows = LabelVideoSegment.objects.filter(video_file=video).values_list(
        "label__name",
        "state__is_validated",
        "start_frame_number",
        "end_frame_number",
    )
    intervals: list[_SegmentInterval] = []
    for label_name, is_validated, start_frame, end_frame in rows:
        intervals.append(
            _SegmentInterval(
                start_frame=int(start_frame),
                end_frame=int(end_frame),
                is_validated_outside=(
                    isinstance(label_name, str)
                    and label_name.lower() == OUTSIDE_LABEL_NAME
                    and is_validated is True
                ),
            )
        )
    return intervals


def _interval_query(intervals: list[_SegmentInterval]) -> Q | None:
    if not intervals:
        return None
    query = Q()
    for interval in intervals:
        query |= Q(
            frame_number__gte=interval.start_frame,
            frame_number__lt=interval.end_frame,
        )
    return query


def _prunable_frame_query(video: VideoFile) -> Q | None:
    """
    Frames inside a validated `outside` segment and outside every other segment.

    Any other segment overlapping an outside interval, whether clinical,
    unvalidated or unlabeled, keeps its frames: conflicting labels fail safe.
    """
    intervals = _segment_intervals(video)
    outside_query = _interval_query(
        [interval for interval in intervals if interval.is_validated_outside]
    )
    if outside_query is None:
        return None
    protected_query = _interval_query(
        [interval for interval in intervals if not interval.is_validated_outside]
    )
    if protected_query is None:
        return outside_query
    return outside_query & ~protected_query


def prune_unused_validated_outside_frames(
    video: VideoFile,
    *,
    limit: int = DEFAULT_UNUSED_FRAME_PRUNE_BATCH_SIZE,
) -> int:
    """
    Gradually prune extracted frame files that are no longer needed after validation.

    Only frames covered exclusively by validated `outside` segments are considered.
    Frames attached to any annotation are preserved; AI datasets reference frames
    only through annotations, so downstream workflows remain recreatable.

    The database marks a frame as not extracted, and commits, before its file is
    deleted, so no reader observes an extracted frame without its file. Annotation
    foreign keys are deferred, so an annotation inserted but not yet committed is
    invisible here; it then attaches to a frame persisted as absent, which frame
    decoding from the canonical video can still serve.
    """
    if limit <= 0:
        return 0

    prunable_query = _prunable_frame_query(video)
    if prunable_query is None:
        return 0

    with transaction.atomic():
        locked_frames = list(
            Frame.objects.select_for_update(of=("self",))
            .filter(video=video, is_extracted=True)
            .filter(prunable_query)
            .exclude(image_classification_annotations__isnull=False)
            .order_by("frame_number")[:limit]
        )
        # The filter above may have used a snapshot taken before a concurrent
        # annotation committed. Re-read now that the rows are locked.
        annotated_frame_ids = set(
            ImageClassificationAnnotation.objects.filter(
                frame_id__in=[frame.pk for frame in locked_frames]
            ).values_list("frame_id", flat=True)
        )
        absent_frame_ids = [
            int(frame.pk)
            for frame in locked_frames
            if frame.pk not in annotated_frame_ids
        ]
        if absent_frame_ids:
            Frame.objects.filter(pk__in=absent_frame_ids).update(is_extracted=False)
    if not absent_frame_ids:
        return 0

    pruned_count = 0
    with transaction.atomic():
        # A frame re-extracted between both transactions is extracted again;
        # the row lock keeps it from changing while its file is deleted.
        frames_to_unlink = list(
            Frame.objects.select_for_update(of=("self",))
            .select_related("video")
            .filter(pk__in=absent_frame_ids, is_extracted=False)
            .order_by("frame_number")
        )
        for frame in frames_to_unlink:
            frame_path = frame.file_path
            if not frame_path.exists():
                continue
            try:
                safe_unlink_file(frame_path, missing_ok=True)
            except OSError:
                logger.exception(
                    "Orphaned frame file retained after marking it absent for video %s: frame_id=%s frame_number=%s",
                    video.pk,
                    frame.pk,
                    frame.frame_number,
                )
                continue
            pruned_count += 1

    logger.info(
        "Pruned unused validated outside frames for video %s: pruned=%s limit=%s marked_absent=%s preserved_after_lock=%s",
        video.pk,
        pruned_count,
        limit,
        len(absent_frame_ids),
        len(annotated_frame_ids),
    )
    return pruned_count

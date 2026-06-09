from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Q

from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.utils.filesystem.file_operations import safe_unlink_file

logger = logging.getLogger(__name__)

DEFAULT_UNUSED_FRAME_PRUNE_BATCH_SIZE = 250


def _validated_outside_frame_query(video: VideoFile) -> Q | None:
    outside_segments = list(
        LabelVideoSegment.objects.filter(
            video_file=video,
            label__name__iexact="outside",
            state__is_validated=True,
        )
        .order_by("start_frame_number")
        .values_list("start_frame_number", "end_frame_number")
    )
    if not outside_segments:
        return None

    query = Q()
    for start_frame, end_frame in outside_segments:
        query |= Q(
            frame_number__gte=int(start_frame),
            frame_number__lt=int(end_frame),
        )
    return query


def prune_unused_validated_outside_frames(
    video: VideoFile,
    *,
    limit: int = DEFAULT_UNUSED_FRAME_PRUNE_BATCH_SIZE,
) -> int:
    """
    Gradually prune extracted frame files that are no longer needed after validation.

    Only validated `outside` segment frames are considered, and frames are preserved if
    they are attached to any annotation or AI dataset so downstream workflows remain
    recreatable.
    """
    if limit <= 0:
        return 0

    outside_query = _validated_outside_frame_query(video)
    if outside_query is None:
        return 0

    candidate_frames = list(
        Frame.objects.filter(video=video, is_extracted=True)
        .filter(outside_query)
        .exclude(image_classification_annotations__isnull=False)
        .exclude(
            image_classification_annotations__image_ai_datasets__isnull=False,
        )
        .order_by("frame_number")
        .distinct()[:limit]
    )
    if not candidate_frames:
        return 0

    pruned_frame_ids: list[int] = []
    pruned_count = 0
    for frame in candidate_frames:
        frame_path = frame.file_path
        if not frame_path.exists():
            pruned_frame_ids.append(int(frame.pk))
            continue
        try:
            safe_unlink_file(frame_path, missing_ok=True)
            pruned_frame_ids.append(int(frame.pk))
            pruned_count += 1
        except Exception:
            logger.exception(
                "Failed pruning unused validated outside frame file for video %s: frame_id=%s frame_number=%s path=%s",
                video.pk,
                frame.pk,
                frame.frame_number,
                frame_path,
            )

    if not pruned_frame_ids:
        return 0

    with transaction.atomic():
        Frame.objects.filter(pk__in=pruned_frame_ids).update(is_extracted=False)

    logger.info(
        "Pruned unused validated outside frames for video %s: pruned=%s limit=%s candidate_count=%s",
        video.pk,
        pruned_count,
        limit,
        len(candidate_frames),
    )
    return pruned_count

"""
Service for synchronizing annotation updates with LabelVideoSegment creation.

This module provides functionality to create user-source LabelVideoSegments
when segment annotations are created or updated.
"""

import logging
from typing import Optional

from django.contrib.auth.models import User
from django.db import transaction

from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.other.information_source import InformationSource
from ..models.label.label_video_segment.label_video_segment import (
    SegmentLabel,
    SegmentPredictionMeta,
)
from .video_files import (
    video_frame_number_to_seconds,
    video_seconds_to_frame_number,
)
from lx_dtypes.models.contracts.video_segments import (
    SegmentAnnotationInput,
    parse_segment_annotation_input,
)

logger = logging.getLogger(__name__)


def create_user_segment_from_annotation(
    annotation: SegmentAnnotationInput | dict[str, object], request_user: User
) -> Optional[LabelVideoSegment]:
    """
    Create a user-source LabelVideoSegment from a segment annotation.

    This function:
    1. Locates the original LabelVideoSegment (if segment_id is present)
    2. Clones all its DB fields
    3. Overwrites with new data from annotation
    4. Sets information_source = user
    5. Saves via model manager

    Args:
        annotation: Annotation data containing segment information
        request_user: The authenticated user making the request

    Returns:
        New LabelVideoSegment instance or None if creation failed/skipped
    """
    annotation_input = parse_segment_annotation_input(annotation)
    if annotation_input is None:
        logger.debug("Annotation is invalid for segment creation, skipping")
        return None

    video_id = annotation_input.video_id
    start_time = annotation_input.start_time
    end_time = annotation_input.end_time
    label_text = annotation_input.text.strip()
    original_segment_id = annotation_input.metadata.segment_id
    label: SegmentLabel = None
    prediction_meta: SegmentPredictionMeta = None
    segment_label: SegmentLabel = None

    try:
        video_file = VideoFile.objects.get(pk=video_id)

        start_frame_number = video_seconds_to_frame_number(video_file, start_time)
        end_frame_number = video_seconds_to_frame_number(video_file, end_time)

        user_source, _ = InformationSource.objects.get_or_create(
            name="user", defaults={"description": "User-generated annotations"}
        )

        label = None
        if label_text:
            try:
                label = Label.objects.filter(name__iexact=label_text).first()
                if not label:
                    for tag in annotation_input.tags:
                        label = Label.objects.filter(name__iexact=tag).first()
                        if label:
                            break
            except Exception as e:
                logger.warning(f"Error finding label '{label_text}': {e}")
        segment_label = label

        if original_segment_id:
            try:
                original_segment = LabelVideoSegment.objects.get(pk=original_segment_id)

                original_start_time = video_frame_number_to_seconds(
                    video_file, original_segment.start_frame_number
                )
                original_end_time = video_frame_number_to_seconds(
                    video_file, original_segment.end_frame_number
                )

                timing_changed = (
                    abs(original_start_time - start_time) > 0.1
                    or abs(original_end_time - end_time) > 0.1
                )

                label_changed = (label and original_segment.label != label) or (
                    not label and original_segment.label is not None
                )

                if not timing_changed and not label_changed:
                    logger.debug(
                        f"No changes detected in segment {original_segment_id}, skipping user segment creation"
                    )
                    return None

                prediction_meta = original_segment.prediction_meta
                segment_label = label or original_segment.label

                logger.info(
                    f"Cloning segment {original_segment_id} with user modifications"
                )

            except LabelVideoSegment.DoesNotExist:
                logger.warning(
                    f"Original segment {original_segment_id} not found, creating new user segment"
                )

        with transaction.atomic():
            new_segment = LabelVideoSegment.create_from_video(
                source=video_file,
                prediction_meta=prediction_meta,
                label=segment_label,
                start_frame_number=start_frame_number,
                end_frame_number=end_frame_number,
            )

            new_segment.source = user_source
            new_segment.save()

            request_username = request_user.get_username()
            logger.info(
                f"Created user segment {new_segment.pk} for video {video_id} by user {request_username}"
            )
            return new_segment

    except VideoFile.DoesNotExist:
        logger.error(f"Video {video_id} not found, cannot create user segment")
        return None
    except Exception as e:
        logger.error(f"Error creating user segment from annotation: {e}")
        return None

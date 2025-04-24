import logging
from typing import TYPE_CHECKING, List, Dict, Tuple
from icecream import ic
from pathlib import Path
from django.db.models import Q # Import Q for complex queries

if TYPE_CHECKING:
    from .video_file import VideoFile
    from ...label import LabelVideoSegment, Label
    from ...metadata import VideoPredictionMeta
    from ..frame import Frame
    from django.db.models import QuerySet

logger = logging.getLogger(__name__)

def _convert_sequences_to_db_segments(
    video: "VideoFile",
    sequences: Dict[str, List[Tuple[int, int]]],
    video_prediction_meta: "VideoPredictionMeta",
):
    """
    Converts predicted sequences into LabelVideoSegment database objects.

    Args:
        video: The VideoFile instance.
        sequences: A dictionary mapping label names to lists of (start_frame, end_frame) tuples.
        video_prediction_meta: The related VideoPredictionMeta instance.
    """
    from ...label import (
        Label,
        LabelVideoSegment,
    )  # Local import for dependency isolation

    created_count = 0
    skipped_count = 0
    error_count = 0

    for label_name, sequence_list in sequences.items():
        try:
            label = Label.objects.get(name=label_name)
        except Label.DoesNotExist:
            logger.warning(
                "Label '%s' not found in database. Skipping sequences for this label for video %s.",
                label_name,
                video.uuid,
            )
            skipped_count += len(sequence_list)
            continue

        # segments_to_create = [] # Not needed with custom_create
        for sequence in sequence_list:
            start_frame_number = sequence[0]
            end_frame_number = sequence[1]

            # Basic validation
            if start_frame_number > end_frame_number or start_frame_number < 0:
                logger.warning(
                    "Invalid sequence [%d, %d] for label '%s' in video %s. Skipping.",
                    start_frame_number,
                    end_frame_number,
                    label_name,
                    video.uuid,
                )
                error_count += 1
                continue

            # Prepare data for LabelVideoSegment creation
            init_dict = {
                "video_file": video,
                "prediction_meta": video_prediction_meta,
                "label": label,
                "start_frame_number": start_frame_number,
                "end_frame_number": end_frame_number,
            }
            try:
                # Assuming custom_create handles creation and saving or returns unsaved instance
                segment = LabelVideoSegment.objects.create(**init_dict)
                # If custom_create doesn't save, uncomment below:
                # segment.save()
                created_count += 1
            except Exception as e:
                logger.error(
                    "Error creating LabelVideoSegment for sequence %s, label '%s', video %s: %s",
                    sequence,
                    label_name,
                    video.uuid,
                    e,
                    exc_info=True,
                )
                error_count += 1

    logger.info(
        "Sequence conversion for video %s completed. Created: %d, Skipped (label not found): %d, Errors: %d",
        video.uuid,
        created_count,
        skipped_count,
        error_count,
    )


def _sequences_to_label_video_segments(
    video: "VideoFile",
    video_prediction_meta: "VideoPredictionMeta",
):
    """Converts stored sequences on the video object to LabelVideoSegments."""
    if not video.sequences:
        return

    if not video_prediction_meta:
        return

    _convert_sequences_to_db_segments(
        video=video,
        sequences=video.sequences,
        video_prediction_meta=video_prediction_meta,
    )

def _get_outside_segments(video: "VideoFile", outside_label_name: str = "outside") -> "QuerySet[LabelVideoSegment]":
    """Gets LabelVideoSegments marked with the 'outside' label."""
    from ...label import Label, LabelVideoSegment # Local import

    try:
        outside_label = Label.objects.get(name__iexact=outside_label_name)
        return video.label_video_segments.filter(label=outside_label)
    except Label.DoesNotExist:
        logger.warning("Label '%s' not found in the database.", outside_label_name)
        return LabelVideoSegment.objects.none()
    except Exception as e:
        logger.error("Error getting outside segments for video %s: %s", video.uuid, e, exc_info=True)
        return LabelVideoSegment.objects.none()

def _get_outside_frames(video: "VideoFile", outside_label_name: str = "outside") -> "QuerySet[Frame]":
    """
    Gets a QuerySet of all unique Frame objects that fall within any segment
    labeled with the specified 'outside_label_name'.
    """
    from ..frame import Frame # Local import

    outside_segments = _get_outside_segments(video, outside_label_name)
    if not outside_segments.exists():
        return Frame.objects.none()

    # Build a Q object to filter frames based on all outside segments
    q_objects = Q()
    for segment in outside_segments:
        q_objects |= Q(frame_number__gte=segment.start_frame_number, frame_number__lt=segment.end_frame_number) # Use lt for end frame

    if not q_objects:
        return Frame.objects.none()

    # Filter the video's frames using the combined Q object
    try:
        return video.frames.filter(q_objects).distinct().order_by('frame_number')
    except Exception as e:
        logger.error("Error filtering outside frames for video %s: %s", video.uuid, e, exc_info=True)
        return Frame.objects.none()

def _get_outside_frame_paths(video: "VideoFile", outside_label_name: str = "outside") -> List["Path"]:
    """Gets the file paths of frames that fall within 'outside' segments."""
    from pathlib import Path # Local import
    frames = _get_outside_frames(video, outside_label_name=outside_label_name)
    frame_paths = []
    for frame in frames:
        try:
            # Access the path attribute of the image FileField
            frame_paths.append(Path(frame.image.path))
        except Exception as e:
            logger.warning("Could not get path for frame %s (Number: %d): %s", frame.pk, frame.frame_number, e)
            ic(f"Could not get path for frame {frame.pk}: {e}")

    logger.info("Found %d frame paths within '%s' segments for video %s", len(frame_paths), outside_label_name, video.uuid)
    return frame_paths

def _label_segments_to_frame_annotations(video: "VideoFile"):
    """Generates frame annotations based on existing LabelVideoSegments."""
    logger.info("Generating frame annotations from segments for video %s", video.uuid)
    processed_count = 0
    try:
        # Access related manager directly
        for lvs in video.label_video_segments.all():
            lvs_duration = lvs.get_segment_len_in_s() # Assuming this method exists
            # Process segments longer than 3 seconds (example condition)
            if lvs_duration >= 3:
                try:
                    lvs.generate_annotations() # Assuming this method exists
                    processed_count += 1
                except Exception as e:
                     logger.error("Error generating annotations for segment %s (Video %s): %s", lvs.pk, video.uuid, e)
        logger.info("Processed %d segments for frame annotations for video %s", processed_count, video.uuid)
    except AttributeError:
         logger.error("Could not generate frame annotations for video %s. 'label_video_segments' related manager not found.", video.uuid)


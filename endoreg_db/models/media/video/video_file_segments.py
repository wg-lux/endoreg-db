import logging
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Set, Tuple

from django.db.models import Q  # Import Q for complex queries
from icecream import ic

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from ...label import LabelVideoSegment
    from ...metadata import VideoPredictionMeta
    from ..frame import Frame
    from .video_file import VideoFile

logger = logging.getLogger(__name__)


def _convert_sequences_to_db_segments(
    video: "VideoFile",
    sequences: Dict[str, List[Tuple[int, int]]],
    video_prediction_meta: "VideoPredictionMeta",
):
    """
    Converts predicted sequences into LabelVideoSegment database objects
    and ensures their corresponding state objects are created.
    """
    from ...label import Label, LabelVideoSegment  # Local import for models

    logger.info("Converting sequences to LabelVideoSegments for video %s, prediction meta %s", video.uuid, video_prediction_meta.pk)
    created_count = 0
    skipped_count = 0
    error_count = 0
    state_created_count = 0
    state_error_count = 0

    processed_labels = set()

    for label_name, sequence_list in sequences.items():
        if not sequence_list:
            continue

        processed_labels.add(label_name)

        try:
            label = Label.objects.get(name=label_name)  # require pre-existing label
        except Exception as e:
            logger.error("Could not get or create Label '%s': %s", label_name, e, exc_info=True)
            error_count += len(sequence_list)
            continue

        segments_to_create = []
        for start_frame, end_frame in sequence_list:
            if start_frame > end_frame or start_frame < 0:
                logger.warning("Skipping invalid sequence for label '%s': start=%d, end=%d", label_name, start_frame, end_frame)
                skipped_count += 1
                continue

            segments_to_create.append(
                LabelVideoSegment(
                    video_file=video,
                    label=label,
                    start_frame_number=start_frame,
                    end_frame_number=end_frame,
                    prediction_meta=video_prediction_meta,
                )
            )

        if segments_to_create:
            try:
                LabelVideoSegment.objects.bulk_create(segments_to_create, ignore_conflicts=True)
                created_count += len(segments_to_create)
                logger.debug("Bulk created %d segments for label '%s'", len(segments_to_create), label_name)
            except Exception as e:
                logger.error("Error bulk creating segments for label '%s': %s", label_name, e, exc_info=True)
                error_count += len(segments_to_create)

    newly_created_segments = LabelVideoSegment.objects.filter(video_file=video, prediction_meta=video_prediction_meta, label__name__in=processed_labels)

    logger.info(
        "Attempting to create state objects for %d potentially new segments (Video: %s, PredictionMeta: %s)",
        newly_created_segments.count(),
        video.uuid,
        video_prediction_meta.pk,
    )

    for segment in newly_created_segments:
        try:
            _state, created = segment.get_or_create_state()
            if created:
                state_created_count += 1
        except Exception as e:
            logger.error("Failed to get or create state for segment %s (Video: %s): %s", segment.pk, video.uuid, e, exc_info=True)
            state_error_count += 1

    logger.info(
        "LabelVideoSegment conversion finished for video %s. Segments Created: %d, Skipped: %d, Errors: %d. States Created: %d, State Errors: %d",
        video.uuid,
        created_count,
        skipped_count,
        error_count,
        state_created_count,
        state_error_count,
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
    from ...label import Label, LabelVideoSegment  # Local import for models

    try:
        outside_label = Label.objects.get(name__iexact=outside_label_name)
        return video.label_video_segments.filter(label=outside_label)
    except Label.DoesNotExist:
        logger.warning("Label '%s' not found in the database.", outside_label_name)
        return LabelVideoSegment.objects.none()
    except AttributeError:
        logger.error("VideoFile instance does not have 'label_video_segments' related manager.")
        return LabelVideoSegment.objects.none()
    except Exception as e:
        logger.error("Error getting '%s' segments for video %s: %s", outside_label_name, video.uuid, e, exc_info=True)
        return LabelVideoSegment.objects.none()


def _get_outside_frame_numbers(video: "VideoFile", outside_label_name: str = "outside") -> Set[int]:
    """
    Gets a set of frame numbers corresponding to segments labeled as 'outside'.
    """
    outside_segments = _get_outside_segments(video, outside_label_name)
    frame_numbers = set()
    for segment in outside_segments:
        frame_numbers.update(range(segment.start_frame_number, segment.end_frame_number + 1))
    if frame_numbers:
        logger.info("Found %d frame numbers marked as '%s' for video %s.", len(frame_numbers), outside_label_name, video.uuid)
    else:
        logger.info("No frame numbers marked as '%s' found for video %s.", outside_label_name, video.uuid)
    return frame_numbers


def _get_outside_frames(video: "VideoFile", outside_label_name: str = "outside") -> "QuerySet[Frame]":
    """
    Gets a QuerySet of all unique Frame objects that fall within any segment
    labeled with the specified 'outside_label_name'.
    """
    from ..frame import Frame  # Local import

    outside_segments = _get_outside_segments(video, outside_label_name)
    if not outside_segments.exists():
        return Frame.objects.none()

    q_objects: Q | None = None
    for segment in outside_segments:
        clause = Q(frame_number__gte=segment.start_frame_number, frame_number__lt=segment.end_frame_number)
        q_objects = clause if q_objects is None else q_objects | clause

    if q_objects is None:
        return Frame.objects.none()

    try:
        return video.frames.filter(q_objects).distinct().order_by("frame_number")
    except Exception as e:
        logger.error("Error filtering outside frames for video %s: %s", video.uuid, e, exc_info=True)
        return Frame.objects.none()


def _get_outside_frame_paths(video: "VideoFile", outside_label_name: str = "outside") -> List["Path"]:
    """Gets the file paths of frames that fall within 'outside' segments."""
    from pathlib import Path  # Local import

    frames = _get_outside_frames(video, outside_label_name=outside_label_name)
    frame_paths = []
    for frame in frames:
        try:
            frame_paths.append(Path(frame.relative_path))
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
        for lvs in video.label_video_segments.all():
            lvs_duration = lvs.get_segment_len_in_s()
            if lvs_duration >= 3:
                try:
                    lvs.generate_annotations()
                    processed_count += 1
                except Exception as e:
                    logger.error("Error generating annotations for segment %s (Video %s): %s", lvs.pk, video.uuid, e)
        logger.info("Processed %d segments for frame annotations for video %s", processed_count, video.uuid)
    except AttributeError:
        logger.error("Could not generate frame annotations for video %s. 'label_video_segments' related manager not found.", video.uuid)

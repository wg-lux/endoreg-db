# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportUnusedClass=false
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from collections.abc import Mapping, Sequence

from django.db.models import Q  # Import Q for complex queries
from icecream import ic
from lx_dtypes.models.contracts.video_segments import (
    VideoSegmentsPayload,
)

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from endoreg_db.models.other.information_source import InformationSource
    from endoreg_db.models.label.label import Label
    from endoreg_db.models.label.label_video_segment.label_video_segment import (
        LabelVideoSegment,
    )
    from endoreg_db.models.metadata.video_prediction_meta import VideoPredictionMeta
    from endoreg_db.models.media.frame.frame import Frame
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)


class _InformationSourceManagerLike(Protocol):
    def get_or_create_by_name(
        self, name: str, **defaults: object
    ) -> tuple["InformationSource", bool]: ...


class _LabelManagerLike(Protocol):
    def resolve_by_name(
        self, name: str, *, case_insensitive: bool = False
    ) -> "Label | None": ...


def get_outside_frames(
    video: "VideoFile",
    outside_label_name: str = "outside",
    only_validated: bool = False,
) -> "QuerySet[Frame]":
    """Public wrapper for frame filtering of outside segments."""
    return _get_outside_frames(
        video,
        outside_label_name=outside_label_name,
        only_validated=only_validated,
    )


def get_outside_segments(
    video: "VideoFile",
    outside_label_name: str = "outside",
    only_validated: bool = False,
) -> "QuerySet[LabelVideoSegment]":
    """Public wrapper for outside segment query."""
    return _get_outside_segments(
        video,
        outside_label_name=outside_label_name,
        only_validated=only_validated,
    )


def _convert_sequences_to_db_segments(
    video: "VideoFile",
    sequences: Mapping[str, Sequence[tuple[int, int]]],
    video_prediction_meta: "VideoPredictionMeta",
) -> None:
    """
    Converts predicted sequences into LabelVideoSegment database objects
    and ensures their corresponding state objects are created.
    """
    from endoreg_db.models.other.information_source import InformationSource

    from endoreg_db.models.label import (
        Label,
        LabelVideoSegment,
    )  # Local import for models

    logger.info(
        "Converting sequences to LabelVideoSegments for video %s, prediction meta %s",
        video.video_hash,
        video_prediction_meta.pk,
    )
    created_count = 0
    skipped_count = 0
    error_count = 0
    state_created_count = 0
    state_error_count = 0

    processed_labels: set[str] = set()
    prediction_source, _ = cast(
        _InformationSourceManagerLike, InformationSource.objects
    ).get_or_create_by_name("prediction")

    for label_name, sequence_list in sequences.items():
        if not sequence_list:
            continue

        processed_labels.add(label_name)

        label: Label | None = None
        model_meta = getattr(video_prediction_meta, "model_meta", None)
        labelset = getattr(model_meta, "labelset", None)
        if labelset is not None:
            label = cast(
                Label | None,
                labelset.labels.filter(name=label_name).order_by("pk").first(),
            )
        if label is None:
            label = cast(_LabelManagerLike, Label.objects).resolve_by_name(
                label_name, case_insensitive=True
            )
        if label is None:
            logger.error(
                "Could not get Label '%s' while converting prediction sequences",
                label_name,
            )
            error_count += len(sequence_list)
            continue

        segments_to_create: list[LabelVideoSegment] = []
        for start_frame, end_frame in sequence_list:
            if start_frame >= end_frame or start_frame < 0:
                logger.warning(
                    "Skipping invalid sequence for label '%s': start=%d, end=%d",
                    label_name,
                    start_frame,
                    end_frame,
                )
                skipped_count += 1
                continue

            if LabelVideoSegment.objects.filter(
                video_file=video,
                label=label,
                prediction_meta=video_prediction_meta,
                start_frame_number=start_frame,
                end_frame_number=end_frame,
            ).exists():
                skipped_count += 1
                continue

            segments_to_create.append(
                LabelVideoSegment(
                    video_file=video,
                    label=label,
                    start_frame_number=start_frame,
                    end_frame_number=end_frame,
                    source=prediction_source,
                    prediction_meta=video_prediction_meta,
                )
            )

        if segments_to_create:
            try:
                LabelVideoSegment.objects.bulk_create(
                    segments_to_create, ignore_conflicts=True
                )
                created_count += len(segments_to_create)
                logger.debug(
                    "Bulk created %d segments for label '%s'",
                    len(segments_to_create),
                    label_name,
                )
            except Exception as e:
                logger.error(
                    "Error bulk creating segments for label '%s': %s",
                    label_name,
                    e,
                    exc_info=True,
                )
                error_count += len(segments_to_create)

    newly_created_segments = LabelVideoSegment.objects.filter(
        video_file=video,
        prediction_meta=video_prediction_meta,
        label__name__in=processed_labels,
    )

    logger.info(
        "Attempting to create state objects for %d potentially new segments (Video: %s, PredictionMeta: %s)",
        newly_created_segments.count(),
        video.video_hash,
        video_prediction_meta.pk,
    )

    for segment in newly_created_segments:
        try:
            _state, created = segment.get_or_create_state()
            if created:
                state_created_count += 1
        except Exception as e:
            logger.error(
                "Failed to get or create state for segment %s (Video: %s): %s",
                segment.pk,
                video.video_hash,
                e,
                exc_info=True,
            )
            state_error_count += 1

    logger.info(
        "LabelVideoSegment conversion finished for video %s. Segments Created: %d, Skipped: %d, Errors: %d. States Created: %d, State Errors: %d",
        video.video_hash,
        created_count,
        skipped_count,
        error_count,
        state_created_count,
        state_error_count,
    )


def convert_sequences_to_db_segments(
    video: "VideoFile",
    sequences: Mapping[str, Sequence[tuple[int, int]]],
    video_prediction_meta: "VideoPredictionMeta",
) -> None:
    """Public service wrapper for temporal prediction segment materialization."""
    _convert_sequences_to_db_segments(
        video=video,
        sequences=sequences,
        video_prediction_meta=video_prediction_meta,
    )


def _sequences_to_label_video_segments(
    video: "VideoFile",
    video_prediction_meta: "VideoPredictionMeta",
) -> None:
    """Converts stored sequences on the video object to LabelVideoSegments."""
    if not video.sequences:
        return

    segments_payload = VideoSegmentsPayload.model_validate(video.sequences)
    _convert_sequences_to_db_segments(
        video=video,
        sequences=segments_payload.as_dict,
        video_prediction_meta=video_prediction_meta,
    )


def _get_outside_segments(
    video: "VideoFile",
    outside_label_name: str = "outside",
    only_validated: bool = False,
) -> "QuerySet[LabelVideoSegment]":
    """Gets LabelVideoSegments marked with the 'outside' label."""
    from endoreg_db.models.label.label_video_segment.label_video_segment import (
        LabelVideoSegment,
    )

    try:
        # FIX: Use direct filter instead of relying on 'label_video_segments' related name
        # which might not exist or might be named differently (e.g. labelvideosegment_set)
        segments = LabelVideoSegment.objects.filter(
            video_file=video,
            label__name__iexact=outside_label_name,
        )
        if only_validated:
            segments = segments.filter(state__is_validated=True)
        return segments
    except Exception as e:
        logger.error(
            "Error getting '%s' segments for video %s: %s",
            outside_label_name,
            video.video_hash,
            e,
            exc_info=True,
        )
        return LabelVideoSegment.objects.none()


def _get_outside_frame_numbers(
    video: "VideoFile",
    outside_label_name: str = "outside",
    only_validated: bool = False,
) -> set[int]:
    """
    Gets a set of frame numbers corresponding to segments labeled as 'outside'.
    """
    outside_segments = _get_outside_segments(
        video,
        outside_label_name,
        only_validated=only_validated,
    )
    frame_numbers: set[int] = set()
    for segment in outside_segments:
        frame_numbers.update(
            range(segment.start_frame_number, segment.end_frame_number + 1)
        )
    if frame_numbers:
        logger.info(
            "Found %d frame numbers marked as '%s' for video %s.",
            len(frame_numbers),
            outside_label_name,
            video.video_hash,
        )
    else:
        logger.info(
            "No frame numbers marked as '%s' found for video %s.",
            outside_label_name,
            video.video_hash,
        )
    return frame_numbers


def _get_outside_frames(
    video: "VideoFile",
    outside_label_name: str = "outside",
    only_validated: bool = False,
) -> "QuerySet[Frame]":
    """
    Gets a QuerySet of all unique Frame objects that fall within any segment
    labeled with the specified 'outside_label_name'.
    """
    from endoreg_db.models.media.frame.frame import Frame  # Local import

    outside_segments = _get_outside_segments(
        video,
        outside_label_name,
        only_validated=only_validated,
    )

    segment_clauses: list[Q] = []
    for segment in outside_segments:
        # FIX: Use __lte for end_frame_number to include the last frame of the segment
        clause = Q(
            frame_number__gte=segment.start_frame_number,
            frame_number__lte=segment.end_frame_number,
        )
        segment_clauses.append(clause)

    if not segment_clauses:
        q_objects = Q(
            image_classification_annotations__label__name__iexact=outside_label_name,
            image_classification_annotations__value=True,
        )
    else:
        q_objects = segment_clauses[0]
        for clause in segment_clauses[1:]:
            q_objects = q_objects | clause
        q_objects = q_objects | Q(
            image_classification_annotations__label__name__iexact=outside_label_name,
            image_classification_annotations__value=True,
        )

    try:
        return video.frames.filter(q_objects).distinct().order_by("frame_number")
    except Exception as e:
        logger.error(
            "Error filtering outside frames for video %s: %s",
            video.video_hash,
            e,
            exc_info=True,
        )
        return Frame.objects.none()


def _get_outside_frame_paths(
    video: "VideoFile",
    outside_label_name: str = "outside",
    only_validated: bool = False,
) -> list[Path]:
    """Gets the file paths of frames that fall within 'outside' segments."""
    frames = _get_outside_frames(
        video,
        outside_label_name=outside_label_name,
        only_validated=only_validated,
    )
    frame_paths: list[Path] = []
    for frame in frames:
        try:
            frame_paths.append(Path(frame.relative_path))
        except Exception as e:
            logger.warning(
                "Could not get path for frame %s (Number: %d): %s",
                frame.pk,
                frame.frame_number,
                e,
            )
            ic(f"Could not get path for frame {frame.pk}: {e}")

    logger.info(
        "Found %d frame paths within '%s' segments for video %s",
        len(frame_paths),
        outside_label_name,
        video.video_hash,
    )
    return frame_paths


def _label_segments_to_frame_annotations(video: "VideoFile") -> None:
    """Generates frame annotations based on existing LabelVideoSegments."""
    logger.info(
        "Generating frame annotations from segments for video %s", video.video_hash
    )
    processed_count = 0
    try:
        # Use getattr to safely access the related manager, or fall back to the default name set
        segments = getattr(
            video, "label_video_segments", getattr(video, "labelvideosegment_set", None)
        )

        if segments:
            for lvs in segments.all():
                lvs_duration = lvs.get_segment_len_in_s()
                if lvs_duration >= 3:
                    try:
                        lvs.generate_annotations()
                        processed_count += 1
                    except Exception as e:
                        logger.error(
                            "Error generating annotations for segment %s (Video %s): %s",
                            lvs.pk,
                            video.video_hash,
                            e,
                        )
        else:
            logger.error(
                "Could not generate frame annotations for video %s. Neither 'label_video_segments' nor 'labelvideosegment_set' related manager found.",
                video.video_hash,
            )

        logger.info(
            "Processed %d segments for frame annotations for video %s",
            processed_count,
            video.video_hash,
        )
    except Exception as e:
        logger.error(
            "Unexpected error generating frame annotations for video %s: %s",
            video.video_hash,
            e,
            exc_info=True,
        )

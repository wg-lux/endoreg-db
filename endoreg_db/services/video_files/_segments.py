# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportUnusedClass=false
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from collections.abc import Mapping, Sequence

from django.db import transaction
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


class PredictionSegmentMaterializationError(RuntimeError):
    """Raised when a prediction result cannot be persisted completely."""


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

    from endoreg_db.models.label.label import Label
    from endoreg_db.models.label.label_video_segment.label_video_segment import (
        LabelVideoSegment,
    )

    logger.info(
        "Converting sequences to LabelVideoSegments for video %s, prediction meta %s",
        video.video_hash,
        video_prediction_meta.pk,
    )
    created_count = 0
    skipped_count = 0
    state_created_count = 0

    processed_labels: set[str] = set()
    expected_segments: set[tuple[int, int, int]] = set()
    segments_to_create: list[tuple[LabelVideoSegment, tuple[int, int, int]]] = []
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
            raise PredictionSegmentMaterializationError(
                "no LabelVideoSegment rows could be materialized for unresolved "
                f"prediction label {label_name!r}."
            )

        for start_frame, end_frame in sequence_list:
            if start_frame >= end_frame or start_frame < 0:
                raise PredictionSegmentMaterializationError(
                    "Invalid prediction sequence for "
                    f"{label_name!r}: start={start_frame}, end={end_frame}."
                )
            identity = (int(label.pk), int(start_frame), int(end_frame))
            if identity in expected_segments:
                skipped_count += 1
                continue
            expected_segments.add(identity)
            segments_to_create.append(
                (
                    LabelVideoSegment(
                        video_file=video,
                        label=label,
                        start_frame_number=start_frame,
                        end_frame_number=end_frame,
                        source=prediction_source,
                        prediction_meta=video_prediction_meta,
                    ),
                    identity,
                )
            )

    with transaction.atomic():
        existing_segments = LabelVideoSegment.objects.filter(
            video_file=video,
            prediction_meta=video_prediction_meta,
            label__name__in=processed_labels,
        )
        existing_identities = {
            (int(label_id), int(start_frame), int(end_frame))
            for label_id, start_frame, end_frame in existing_segments.values_list(
                "label_id", "start_frame_number", "end_frame_number"
            )
        }
        pending_segments = [
            segment
            for segment, identity in segments_to_create
            if identity not in existing_identities
        ]
        if pending_segments:
            LabelVideoSegment.objects.bulk_create(pending_segments)
            created_count = len(pending_segments)

        materialized_segments = LabelVideoSegment.objects.filter(
            video_file=video,
            prediction_meta=video_prediction_meta,
            label__name__in=processed_labels,
        )
        materialized_identities = {
            (int(label_id), int(start_frame), int(end_frame))
            for label_id, start_frame, end_frame in materialized_segments.values_list(
                "label_id", "start_frame_number", "end_frame_number"
            )
        }
        missing_segments = expected_segments - materialized_identities
        if missing_segments:
            raise PredictionSegmentMaterializationError(
                "Prediction segment materialization was incomplete; missing "
                f"identities: {sorted(missing_segments)!r}."
            )

        for segment in materialized_segments:
            _state, created = segment.get_or_create_state()
            if created:
                state_created_count += 1

    logger.info(
        "LabelVideoSegment conversion finished for video %s. Segments Created: %d, Skipped: %d. States Created: %d",
        video.video_hash,
        created_count,
        skipped_count,
        state_created_count,
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

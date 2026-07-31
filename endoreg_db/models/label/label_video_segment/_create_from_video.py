from __future__ import annotations

from types import NoneType
from typing import TYPE_CHECKING, TypeAlias, TypeVar

if TYPE_CHECKING:
    from endoreg_db.models.label.label import Label
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.models.metadata.video_prediction_meta import VideoPredictionMeta
    from .label_video_segment import LabelVideoSegment

__all__ = ["_create_from_video"]

NoPredictionMetaValue: TypeAlias = NoneType
NoSegmentLabelValue: TypeAlias = NoneType
SegmentPredictionMeta: TypeAlias = "VideoPredictionMeta | NoPredictionMetaValue"
SegmentLabel: TypeAlias = "Label | NoSegmentLabelValue"
LabelVideoSegmentT = TypeVar("LabelVideoSegmentT", bound="LabelVideoSegment")


def _create_from_video(
    cls: type[LabelVideoSegmentT],
    source: "VideoFile",
    prediction_meta: SegmentPredictionMeta,
    label: SegmentLabel,
    start_frame_number: int,
    end_frame_number: int,
) -> LabelVideoSegmentT:
    """
    Create a LabelVideoSegment instance from a VideoFile.
    """
    if start_frame_number < 0 or end_frame_number < 0:
        raise ValueError(
            f"Frame numbers must be non-negative: start={start_frame_number}, end={end_frame_number}"
        )

    if start_frame_number > end_frame_number:
        raise ValueError(
            f"Start frame number ({start_frame_number}) must be less than or equal to end frame number ({end_frame_number})"
        )

    segment = cls(
        start_frame_number=start_frame_number,
        end_frame_number=end_frame_number,
        label=label,
        video_file=source,
        prediction_meta=prediction_meta,
    )
    return segment

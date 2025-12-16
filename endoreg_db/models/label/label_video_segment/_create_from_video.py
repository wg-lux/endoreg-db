from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from endoreg_db.models import Label, VideoFile, VideoPredictionMeta

__all__ = ["_create_from_video"]


def _create_from_video(
    cls,
    source: "VideoFile",
    prediction_meta: Optional["VideoPredictionMeta"],
    label: Optional["Label"],
    start_frame_number: int,
    end_frame_number: int,
):
    """
    Create a LabelVideoSegment instance from a VideoFile.
    """
    from endoreg_db.models import VideoFile

    if not isinstance(source, VideoFile):
        raise ValueError("Source must be a VideoFile instance.")

    if start_frame_number < 0 or end_frame_number < 0:
        raise ValueError(f"Frame numbers must be non-negative: start={start_frame_number}, end={end_frame_number}")

    if start_frame_number > end_frame_number:
        raise ValueError(f"Start frame number ({start_frame_number}) must be less than or equal to end frame number ({end_frame_number})")

    segment = cls(
        start_frame_number=start_frame_number,
        end_frame_number=end_frame_number,
        label=label,
        video_file=source,
        prediction_meta=prediction_meta,
    )
    return segment

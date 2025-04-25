from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from endoreg_db.models import Label, VideoPredictionMeta, VideoFile


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

    segment = cls(
        start_frame_number=start_frame_number,
        end_frame_number=end_frame_number,
        label=label,
        video_file=source,
        prediction_meta=prediction_meta,
    )
    return segment

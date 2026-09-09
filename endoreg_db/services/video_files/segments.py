from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models.label.label_video_segment.label_video_segment import (
        LabelVideoSegment,
    )
    from endoreg_db.models.media.video.video_file import VideoFile


def get_video_outside_segments(
    video: "VideoFile",
    *,
    only_validated: bool = False,
) -> models.QuerySet["LabelVideoSegment"]:
    try:
        segments = video.label_video_segments.filter(label__name__iexact="outside")
        if only_validated:
            segments = segments.filter(state__is_validated=True)
        return segments
    except Exception:
        return video.label_video_segments.none()

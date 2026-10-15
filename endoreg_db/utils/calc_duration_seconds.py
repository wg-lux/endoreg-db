from __future__ import annotations

import math
from typing import TYPE_CHECKING

from endoreg_db.schemas.persisted_json import VideoFileMetaPayload

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def _calc_duration_vf(obj: VideoFile) -> float:
    timeline = VideoFileMetaPayload.model_validate(obj.meta or {}).source_timeline
    if timeline is not None:
        if (
            timeline.artifact_kind == "processed"
            and timeline.content_hash != obj.processed_video_hash
        ):
            raise ValueError("Duration belongs to another processed generation.")
        return timeline.source.timeline.duration_seconds
    if obj.duration is None or not math.isfinite(obj.duration) or obj.duration <= 0:
        raise ValueError("Video requires a persisted positive duration.")
    return float(obj.duration)

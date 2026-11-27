from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.frame_annotation import (
    LabelVideoSegmentLike,
    SegmentAnnotationSnapshot,
    delete_frame_annotations_for_segment as _delete_frame_annotations_for_segment,
)
from endoreg_db.models.state.frame_annotation import (
    sync_frame_annotations_for_segment as _sync_frame_annotations_for_segment,
)

SegmentSnapshot = Mapping[str, Any]


def sync_frame_annotations_for_segment(
    *,
    segment: LabelVideoSegment,
    old_snapshot: SegmentSnapshot | None = None,
) -> None:
    _sync_frame_annotations_for_segment(
        segment=cast(LabelVideoSegmentLike, segment),
        old_snapshot=cast(SegmentAnnotationSnapshot | None, old_snapshot),
    )


def delete_frame_annotations_for_segment(
    *,
    video: VideoFile,
    start_frame_number: int,
    end_frame_number: int,
    label: Label | None,
    information_source_id: int | None,
    model_meta_id: int | None,
) -> int:
    return int(
        _delete_frame_annotations_for_segment(
            video=video,
            start_frame_number=start_frame_number,
            end_frame_number=end_frame_number,
            label=label,
            information_source_id=information_source_id,
            model_meta_id=model_meta_id,
        )
    )


__all__ = [
    "delete_frame_annotations_for_segment",
    "sync_frame_annotations_for_segment",
]

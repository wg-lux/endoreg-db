from __future__ import annotations

from collections.abc import Iterable

from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.state.label_video_segment import LabelVideoSegmentState


def ensure_label_video_segment_states(
    segments: Iterable[LabelVideoSegment],
) -> None:
    segment_ids: list[int] = []
    seen_ids: set[int] = set()
    for segment in segments:
        segment_pk = segment.pk
        if not isinstance(segment_pk, int) or isinstance(segment_pk, bool):
            continue
        if segment_pk in seen_ids:
            continue
        seen_ids.add(segment_pk)
        segment_ids.append(segment_pk)

    if not segment_ids:
        return

    existing_ids = {
        int(origin_id)
        for origin_id in LabelVideoSegmentState.objects.filter(
            origin_id__in=segment_ids,
        ).values_list("origin_id", flat=True)
        if origin_id is not None
    }
    missing_states = [
        LabelVideoSegmentState(origin_id=segment_id)
        for segment_id in segment_ids
        if segment_id not in existing_ids
    ]
    if missing_states:
        LabelVideoSegmentState.objects.bulk_create(
            missing_states,
            ignore_conflicts=True,
        )

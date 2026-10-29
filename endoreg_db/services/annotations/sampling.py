"""Share eligibility work and random-sampling pools for one task batch."""

from __future__ import annotations

import random
from typing import cast

from endoreg_db.models.state.frame_annotation import (
    FrameAnnotationQueueSpec,
    FrameLike,
    build_frame_task_queryset,
    ai_dataset_requires_raw_frames,
    pick_random_frame,
)


class FrameQueueSampler:
    def __init__(self, spec: FrameAnnotationQueueSpec, candidate_ids: set[int] | None):
        self.spec = spec
        self.candidate_ids = candidate_ids
        self._eligible_ids: set[int] | None = None
        self._pools: dict[int, list[int]] = {}
        self._query = build_frame_task_queryset(
            video_id=spec.video_id,
            filter_label_id=spec.filter_label.pk
            if spec.filter_label is not None
            else None,
            information_source_name=spec.information_source_name,
            annotator=spec.annotator,
            exclude_annotated=spec.exclude_annotated,
            target_label_id=spec.target_label.pk
            if spec.target_label is not None
            else None,
            require_extracted_frames=spec.require_extracted_frames,
            require_raw_video=spec.require_raw_video
            or ai_dataset_requires_raw_frames(spec.ai_dataset),
            require_processed_video=spec.require_processed_video,
            require_streamable_video_artifact=spec.require_streamable_video_artifact,
            exclude_frame_ids=spec.exclude_frame_ids,
        )

    def pick(
        self, candidate_ids: set[int] | None, excluded_ids: set[int]
    ) -> FrameLike | None:
        if self.candidate_ids is None:
            return pick_random_frame(
                spec=self.spec,
                candidate_frame_ids=candidate_ids,
                exclude_frame_ids=excluded_ids,
            )
        if self._eligible_ids is None:
            self._eligible_ids = set(
                self._query.filter(pk__in=self.candidate_ids)
                .order_by()
                .values_list("pk", flat=True)
                .iterator()
            )
        key = id(candidate_ids) if candidate_ids is not None else 0
        if key not in self._pools:
            ids = (
                self._eligible_ids
                if candidate_ids is None
                else self._eligible_ids.intersection(candidate_ids)
            )
            self._pools[key] = sorted(ids)
        pool = self._pools[key]
        while pool:
            index = random.randint(0, len(pool) - 1)
            frame_id = pool[index]
            pool[index] = pool[-1]
            pool.pop()
            if frame_id in excluded_ids:
                continue
            # Recheck mutable eligibility for the selected row, without the large IN list.
            frame = self._query.filter(pk=frame_id).first()
            if frame is not None:
                return cast(FrameLike, frame)
        return None

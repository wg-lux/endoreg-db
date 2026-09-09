"""Prepare an operator-requested shortlist; predictions remain review suggestions."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from django.utils import timezone
from lx_dtypes.models.contracts.ai_dataset import (
    AIDataSetActiveLearningCandidateContract,
    AIDataSetActiveLearningConfigContract,
    AIDataSetActiveLearningSelectionContract,
)
from pydantic import BaseModel, ConfigDict, Field

from endoreg_db.models.aidataset.aidataset import AIDataSet
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.metadata.model_meta import ModelMeta
from endoreg_db.services.aidataset_active_learning import (
    select_active_learning_frame_indices_from_candidates,
)
from endoreg_db.services.frames.training_images import validate_processed_training_frame
from endoreg_db.utils.filesystem.file_operations import atomic_create_file
from endoreg_db.utils.paths import ensure_within_protected_root


class ActiveLearningReviewShortlist(BaseModel):
    """Endoreg identities surrounding the shared selection contract, never labels."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"] = "1.0"
    review_status: Literal["pending_human_review"] = "pending_human_review"
    created_at: datetime
    dataset_id: int = Field(gt=0)
    model_meta_id: int = Field(gt=0)
    label_ids: list[int]
    label_names: list[str]
    selection: AIDataSetActiveLearningSelectionContract


def build_active_learning_review_shortlist(
    *,
    dataset_id: int,
    model_meta_id: int,
    candidates: list[AIDataSetActiveLearningCandidateContract],
    config: AIDataSetActiveLearningConfigContract,
) -> ActiveLearningReviewShortlist:
    if dataset_id <= 0 or model_meta_id <= 0:
        raise ValueError("dataset_id and model_meta_id must be positive.")
    dataset = AIDataSet.objects.get(pk=dataset_id)
    if (
        dataset.dataset_type != AIDataSet.DATASET_TYPE_IMAGE
        or dataset.ai_model_type != AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL
    ):
        raise ValueError("Active learning requires an image multilabel dataset.")
    model_meta = ModelMeta.objects.select_related("labelset").get(pk=model_meta_id)
    labels = cast(LabelSet, model_meta.labelset).get_labels_in_order()
    if not labels:
        raise ValueError("Active learning model labelset must not be empty.")
    if not candidates:
        raise ValueError("Active learning requires at least one candidate.")
    frame_ids = [candidate.frame_id for candidate in candidates]
    frames = Frame.objects.select_related("video__state").in_bulk(frame_ids)
    annotated_ids = set(
        dataset.image_annotations.filter(frame_id__in=frame_ids).values_list(
            "frame_id", flat=True
        )
    )
    segments = list(
        dataset.video_annotations.filter(
            video_file_id__in={candidate.video_id for candidate in candidates}
        )
    )
    for candidate in candidates:
        frame = frames.get(candidate.frame_id)
        if frame is None or (
            frame.video.pk != candidate.video_id
            or frame.frame_number != candidate.frame_number
            or frame.timestamp != candidate.timestamp
        ):
            raise ValueError(
                "Candidate identity or presentation timestamp does not match its persisted frame."
            )
        if candidate.frame_id not in annotated_ids and not any(
            segment.video_file.pk == candidate.video_id
            and segment.start_frame_number is not None
            and segment.end_frame_number is not None
            and segment.start_frame_number
            <= candidate.frame_number
            < segment.end_frame_number
            for segment in segments
        ):
            raise ValueError(
                "Candidate frame is outside the selected dataset annotation scope."
            )
        if len(candidate.probs) != len(labels):
            raise ValueError(
                "Candidate probabilities must match the ordered model labelset."
            )
        validate_processed_training_frame(frame)
    selection = select_active_learning_frame_indices_from_candidates(
        candidates, config=config
    )
    return ActiveLearningReviewShortlist(
        created_at=timezone.now(),
        dataset_id=dataset_id,
        model_meta_id=model_meta_id,
        label_ids=[label.pk for label in labels],
        label_names=[label.name for label in labels],
        selection=selection,
    )


def write_active_learning_review_shortlist(
    shortlist: ActiveLearningReviewShortlist,
    destination: Path,
) -> Path:
    destination = ensure_within_protected_root(destination)
    if destination.exists():
        raise ValueError(
            "Shortlist output must be a new file; existing evidence cannot be overwritten."
        )
    payload = (
        json.dumps(shortlist.model_dump(mode="json"), allow_nan=False, indent=2) + "\n"
    ).encode("utf-8")
    return atomic_create_file(
        destination=destination,
        content=[payload],
        required_bytes=len(payload),
        file_mode=0o600,
        dir_mode=0o700,
    )

# endoreg_db/utils/ai/data_loader_for_model_training.py

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, TypedDict

from django.db import models

from endoreg_db.models import (
    AIDataSet,
    Frame,
    ImageClassificationAnnotation,
    Label,
    LabelSet,
)


class ImageMultilabelDataset(TypedDict):
    """
    In-memory representation of an image multi-label training dataset.

    All lists are aligned by index:

        image_paths[i]   -> path to image file for sample i
        label_vectors[i] -> list[int|None] of length == len(labels)
        label_masks[i]   -> list[int]       of length == len(labels)

    Where:
        - label_vectors[i][j] is:
            1     -> positive annotation (value=True)
            0     -> negative annotation (value=False)
            None  -> UNKNOWN (no annotation for that (frame, label))

        - label_masks[i][j] is:
            1 -> this entry participates in the loss (0 or 1 is known)
            0 -> IGNORE in the loss (value was None)
    """

    # type description of the returned dict.

    image_paths: List[str]
    label_vectors: List[List[Optional[int]]]
    label_masks: List[List[int]]
    labels: List[Label]
    labelset: LabelSet

    # New: keep track of which DB rows were used, and their legacy exam ids
    frame_ids: List[int]  # Frame.pk for each sample
    old_examination_ids: List[Optional[int]]  # may be None if not set


def _infer_labelset_from_annotations(
    annotations_qs: models.QuerySet[ImageClassificationAnnotation],
) -> LabelSet:
    """
    Try to infer a unique LabelSet from the labels used in the annotations.

    Strategy:
        1. Collect all distinct label_ids from the annotations.
        2. Fetch all Label objects + their label_sets.
        3. Compute the intersection of all label_sets across all labels.
        4. If there is exactly ONE common LabelSet, return it.
           Otherwise, raise NotImplementedError for now.
    """
    label_ids = list(annotations_qs.values_list("label_id", flat=True).distinct())
    if not label_ids:
        raise ValueError("Cannot infer LabelSet: annotations queryset has no labels.")

    labels_qs = Label.objects.filter(id__in=label_ids).prefetch_related("label_sets")
    labelsets_for_each_label = []

    for lbl in labels_qs:
        # lbl.label_sets is the reverse of LabelSet.labels M2M
        ls_ids = list(lbl.label_sets.values_list("id", flat=True))
        if not ls_ids:
            # This label is not part of any LabelSet -> ambiguous
            raise NotImplementedError(
                f"Label id={lbl.id}, name='{lbl.name}' is not part of any LabelSet. "
                "Explicit LabelSet selection is required."
            )
        labelsets_for_each_label.append(set(ls_ids))

    # Intersection of all labelset id sets
    common_ids = set.intersection(*labelsets_for_each_label)
    if not common_ids:
        raise NotImplementedError(
            "No common LabelSet across all labels in this AIDataSet. "
            "Please specify a LabelSet explicitly."
        )
    if len(common_ids) > 1:
        raise NotImplementedError(
            "More than one common LabelSet found for the labels in this AIDataSet. "
            "Please specify a LabelSet explicitly to disambiguate."
        )

    ls_id = next(iter(common_ids))
    return LabelSet.objects.get(id=ls_id)


def build_image_multilabel_dataset_from_db(
    dataset: AIDataSet,
    labelset: Optional[LabelSet] = None,
) -> ImageMultilabelDataset:
    """
    Build an in-memory multilabel dataset for an IMAGE-based AIDataSet.

    Steps:
        1. Take all ImageClassificationAnnotation rows linked to this AIDataSet
           (via dataset.image_annotations M2M).
        2. Determine the LabelSet (either explicitly given or inferred).
        3. For each used Frame, build:
            - an image path
            - a label vector (1, 0, or None for each label in LabelSet)
            - a mask vector (1 where known, 0 where unknown)
        4. Return a dict that can be wrapped in a torch/tf Dataset.

    NOTE:
        - This function does NOT write anything to the DB.
        - It only reads DB rows and returns Python structures.
    """
    if dataset.dataset_type != AIDataSet.DATASET_TYPE_IMAGE:
        raise ValueError(
            f"build_image_multilabel_dataset_from_db expected dataset_type='image', "
            f"got '{dataset.dataset_type}' for AIDataSet id={dataset.id}."
        )

    # Get the annotation relation dynamically (for future video/text types)
    annotations_qs = dataset.get_annotations_queryset().select_related("frame", "label")

    if annotations_qs.count() == 0:
        raise ValueError(
            f"AIDataSet id={dataset.id} has no annotations attached. "
            "Make sure your import script populated image_annotations."
        )

    # Decide which LabelSet to use
    if labelset is None:
        labelset = _infer_labelset_from_annotations(annotations_qs)

    # Fixed label order (= fixed column order for the label vectors)
    labels_in_order: List[Label] = labelset.get_labels_in_order()
    if not labels_in_order:
        raise ValueError(
            f"LabelSet id={labelset.id}, name='{labelset.name}' has no labels."
        )

    num_labels = len(labels_in_order)
    label_index: Dict[int, int] = {
        lbl.id: idx for idx, lbl in enumerate(labels_in_order)
    }

    # Group annotations by frame
    anns_by_frame: Dict[int, List[ImageClassificationAnnotation]] = defaultdict(list)
    frames_order: List[int] = []

    for ann in annotations_qs:
        frame_id = ann.frame_id
        if frame_id not in anns_by_frame:
            frames_order.append(frame_id)
        anns_by_frame[frame_id].append(ann)

    # Build vectors
    image_paths: List[str] = []
    label_vectors: List[List[Optional[int]]] = []
    label_masks: List[List[int]] = []

    # New: id tracking for splitting / logging
    frame_ids: List[int] = []
    old_examination_ids: List[Optional[int]] = []

    # Cache frames to avoid repeated DB hits
    frame_obj_by_id: Dict[int, Frame] = {}

    for frame_id in frames_order:
        frame_annotations = anns_by_frame[frame_id]

        # Resolve frame object (from first annotation of this frame)
        frame = frame_obj_by_id.get(frame_id)
        if frame is None:
            frame = frame_annotations[0].frame
            frame_obj_by_id[frame_id] = frame

            # New: remember DB ids for this sample
        frame_ids.append(frame_id)
        old_examination_ids.append(getattr(frame, "old_examination_id", None))

        # Start with unknown for all labels
        vec: List[Optional[int]] = [None] * num_labels

        # Fill with 1/0 where we have annotations
        for ann in frame_annotations:
            idx = label_index.get(ann.label_id)
            if idx is None:
                # Label not part of this LabelSet: ignore
                continue
            vec[idx] = 1 if ann.value else 0

        # Build mask: 1 where vec is known, 0 where unknown
        mask: List[int] = [0 if v is None else 1 for v in vec]

        # Resolve absolute image path from the Frame model
        file_path: Path = frame.file_path
        image_paths.append(str(file_path))
        label_vectors.append(vec)
        label_masks.append(mask)

    return ImageMultilabelDataset(
        image_paths=image_paths,
        label_vectors=label_vectors,
        label_masks=label_masks,
        labels=labels_in_order,
        labelset=labelset,
        frame_ids=frame_ids,
        old_examination_ids=old_examination_ids,
    )


def build_dataset_for_training(
    dataset: AIDataSet,
    labelset: Optional[LabelSet] = None,
):
    """
    High-level entry point to build a training dataset from an AIDataSet row.

    It inspects:
        - dataset.dataset_type
        - dataset.ai_model_type

    and dispatches to the appropriate builder.

    For now, we support:
        - dataset_type = "image"
        - ai_model_type = "image_multilabel_classification"

    Later, you can extend this to:
        - video segmentation
        - text classification
        etc.
    """
    # IMAGE MULTILABEL CASE
    if (
        dataset.dataset_type == AIDataSet.DATASET_TYPE_IMAGE
        and dataset.ai_model_type == AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL
    ):
        return build_image_multilabel_dataset_from_db(dataset, labelset=labelset)

    # FUTURE EXTENSIONS (example structure, not yet implemented):
    # if dataset.dataset_type == AIDataSet.DATASET_TYPE_VIDEO and \
    #    dataset.ai_model_type == AIDataSet.AI_MODEL_TYPE_VIDEO_SEGMENTATION:
    #     return build_video_segmentation_dataset_from_db(dataset, labelset=labelset)
    #
    # if dataset.dataset_type == AIDataSet.DATASET_TYPE_TEXT and \
    #    dataset.ai_model_type == AIDataSet.AI_MODEL_TYPE_TEXT_CLASSIFICATION:
    #     return build_text_classification_dataset_from_db(dataset, labelset=labelset)

    raise NotImplementedError(
        f"No dataset builder implemented for "
        f"dataset_type='{dataset.dataset_type}', "
        f"ai_model_type='{dataset.ai_model_type}'."
    )

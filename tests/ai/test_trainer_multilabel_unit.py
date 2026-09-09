from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
import torch

from endoreg_db.utils.ai.model_training.losses import (
    compute_class_weights,
    focal_loss_with_mask,
)
from endoreg_db.utils.ai.model_training.model_backbones import create_multilabel_model
from endoreg_db.utils.ai.model_training.trainer_gastronet_multilabel import (
    filter_labels_by_labelset_version,
    groupwise_split_indices_by_video,
)


class _LabelSetQuery:
    def __init__(self, versions: set[int]) -> None:
        self._versions = versions

    def filter(self, **kwargs: Any) -> "_LabelSetVersionQuery":
        return _LabelSetVersionQuery(
            version=kwargs.get("version"),
            versions=self._versions,
        )


@dataclass
class _LabelSetVersionQuery:
    version: int | None
    versions: set[int]

    def exists(self) -> bool:
        return self.version in self.versions


@dataclass
class _LabelStub:
    name: str
    label_sets: _LabelSetQuery


def test_groupwise_split_by_video_keeps_groups_intact() -> None:
    train, val, test = groupwise_split_indices_by_video(
        frame_ids=[10, 11, 12, 13],
        video_ids=[1, 1, 2, 3],
        val_split=0.25,
        test_split=0.25,
        seed=7,
    )

    assert set(train).issubset({0, 1, 2, 3})
    assert set(val).issubset({0, 1, 2, 3})
    assert set(test).issubset({0, 1, 2, 3})
    assert set(train).union(val, test) == {0, 1, 2, 3}

    assignments: dict[int, str] = {}
    for index in [0, 1, 2, 3]:
        if index in train:
            assignments[index] = "train"
        elif index in val:
            assignments[index] = "val"
        else:
            assignments[index] = "test"

    video_for_index: dict[int, int] = {0: 1, 1: 1, 2: 2, 3: 3}
    for video_id in set(video_for_index.values()):
        video_sets = {
            assignments[index]
            for index, row_video_id in video_for_index.items()
            if row_video_id == video_id
        }
        assert len(video_sets) == 1


def test_filter_labels_by_labelset_version_filters_matching_labels() -> None:
    labels = [
        _LabelStub(name="target-a", label_sets=_LabelSetQuery({1})),
        _LabelStub(name="missing", label_sets=_LabelSetQuery({2})),
        _LabelStub(name="target-b", label_sets=_LabelSetQuery({1})),
    ]
    label_vectors = [
        [1, 0, 0],
        [0, 1, 1],
        [1, 1, 1],
    ]
    label_masks = [
        [1, 1, 1],
        [1, 1, 1],
        [1, 1, 1],
    ]

    filtered_vectors, filtered_masks, filtered_labels, kept_indices = (
        filter_labels_by_labelset_version(
            labels=labels,
            label_vectors=label_vectors,
            label_masks=label_masks,
            target_version=1,
        )
    )

    # 1. Kept indices are the 1st and 3rd labels
    assert kept_indices == [0, 2]

    # 2. Filtered labels contain only the matching label objects
    assert len(filtered_labels) == 2
    assert filtered_labels[0].name == "target-a"
    assert filtered_labels[1].name == "target-b"

    # 3. Vectors keep ALL 3 rows, but filter out the middle column
    assert filtered_vectors == [
        [1, 0],  # From [1, 0, 0]
        [0, 1],  # From [0, 1, 1]
        [1, 1],  # From [1, 1, 1]
    ]

    # 4. Masks also keep ALL 3 rows, filtering out the middle column
    assert filtered_masks == [
        [1, 1],  # From [1, 1, 1]
        [1, 1],  # From [1, 1, 1]
        [1, 1],  # From [1, 1, 1]
    ]


def test_filter_labels_by_labelset_version_raises_for_empty_selection() -> None:
    labels = [_LabelStub(name="mismatch", label_sets=_LabelSetQuery({3}))]
    with pytest.raises(ValueError):
        filter_labels_by_labelset_version(
            labels=labels,
            label_vectors=[[1]],
            label_masks=[[1]],
            target_version=1,
        )


def test_compute_class_weights_normalizes_to_unit_mean() -> None:
    labels = torch.tensor(
        [
            [1.0, 0.0],
            [1.0, 1.0],
            [0.0, 1.0],
        ],
        dtype=torch.float32,
    )
    masks = torch.tensor(
        [
            [1.0, 1.0],
            [1.0, 0.0],
            [1.0, 1.0],
        ],
        dtype=torch.float32,
    )

    weights = compute_class_weights(labels=labels, masks=masks, eps=1e-6)

    assert weights.shape == torch.Size([2])
    assert torch.isclose(weights.mean(), torch.tensor(1.0), atol=1e-5)


def test_focal_loss_with_mask_applies_masks() -> None:
    logits = torch.tensor([[0.2, -1.0, 0.1]], dtype=torch.float32)
    targets = torch.tensor([[1.0, 0.0, 1.0]], dtype=torch.float32)
    masks = torch.tensor([[1.0, 1.0, 0.0]], dtype=torch.float32)

    loss = focal_loss_with_mask(
        logits=logits,
        targets=targets,
        masks=masks,
        class_weights=None,
        alpha=0.25,
        gamma=2.0,
    )

    assert loss.item() >= 0.0
    assert torch.isfinite(loss)


def test_create_multilabel_model_invalid_backbone_name() -> None:
    with pytest.raises(ValueError):
        create_multilabel_model(
            backbone_name="definitely_unknown",
            num_labels=4,
            backbone_checkpoint=None,
            freeze_backbone=True,
        )

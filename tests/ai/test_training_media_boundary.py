from __future__ import annotations

# pyright: reportPrivateUsage=false

from typing import cast
from unittest.mock import patch

import pytest
import torch
from django.test import override_settings
from PIL import Image

from endoreg_db.models.aidataset.aidataset import AIDataSet
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.utils.ai.model_training import trainer_gastronet_multilabel as trainer
from endoreg_db.utils.ai.model_training.config import TrainingConfig
from endoreg_db.utils.ai.model_training.dataset import EndoMultiLabelDataset


pytestmark = pytest.mark.no_db


@pytest.mark.parametrize("frame_ids", [None, [], [0], [-1], [True]])
def test_dataset_requires_real_frame_identity(frame_ids: list[int] | None) -> None:
    with pytest.raises(ValueError, match="frame_ids"):
        EndoMultiLabelDataset(["cached-image.jpg"], [[1]], [[1]], frame_ids=frame_ids)


@pytest.mark.parametrize(
    ("labels", "masks"),
    cast(
        list[tuple[object, object]],
        [
            ([[None]], [[1]]),
            ([[2]], [[1]]),
            ([[0.5]], [[1]]),
            ([["1"]], [[1]]),
            ([[1]], [[0.5]]),
            ([[1]], [[2]]),
            ([[1]], [[]]),
            ([[]], [[]]),
            ([[1]], []),
        ],
    ),
)
def test_dataset_rejects_malformed_labels_before_reading(
    labels: object, masks: object
) -> None:
    with pytest.raises(ValueError):
        EndoMultiLabelDataset(
            ["cached-image.jpg"],
            cast(list[list[int | None]], labels),
            cast(list[list[int]], masks),
            frame_ids=[1],
        )


def test_unknown_labels_remain_masked_in_subsets() -> None:
    dataset = EndoMultiLabelDataset(
        ["first.jpg", "second.jpg"],
        [[None, 1], [0, None]],
        [[0, 1], [1, 0]],
        frame_ids=[1, 2],
    )
    subset = trainer._subset_dataset(dataset, [1, 0])
    assert subset.frame_ids == [2, 1]
    assert torch.equal(subset.labels, torch.tensor([[0, 0], [0, 1]]))
    assert torch.equal(subset.masks, torch.tensor([[1, 0], [0, 1]]))


@pytest.mark.parametrize(
    "role", ["standalone", "site_node", "local_study_server", "central_hub"]
)
def test_dataset_uses_processed_reader_in_every_deployment(role: str) -> None:
    frame = Frame(pk=12)
    dataset = EndoMultiLabelDataset(
        ["arbitrary-cache-path.jpg"], [[1]], [[1]], image_size=8, frame_ids=[12]
    )
    with (
        override_settings(ENDOREG_DEPLOYMENT_ROLE=role),
        patch.object(Frame.objects, "select_related") as query,
        patch(
            "endoreg_db.services.frames.training_images.read_processed_training_image",
            return_value=Image.new("RGB", (8, 8)),
        ) as read_image,
        patch(
            "PIL.Image.open",
            side_effect=AssertionError("Cached paths must not be opened"),
        ),
    ):
        query.return_value.get.return_value = frame
        image, labels, masks = dataset[0]
    read_image.assert_called_once_with(frame)
    query.return_value.get.assert_called_once_with(pk=12)
    assert tuple(image.shape) == (3, 8, 8)
    assert torch.equal(labels, torch.tensor([1]))
    assert torch.equal(masks, torch.tensor([1]))


def test_unsafe_processed_media_cannot_fall_back_to_cache() -> None:
    frame = Frame(pk=12, video=VideoFile())
    dataset = EndoMultiLabelDataset(["cached-image.jpg"], [[1]], [[1]], frame_ids=[12])
    with (
        patch.object(Frame.objects, "select_related") as query,
        patch(
            "PIL.Image.open",
            side_effect=AssertionError("No image read before approval"),
        ),
    ):
        query.return_value.get.return_value = frame
        with pytest.raises(ValueError, match="validated, export-ready"):
            dataset[0]


@pytest.mark.parametrize(
    ("frames", "videos"),
    [([], []), ([1], []), ([0], [1]), ([1], [True]), ([1, 2], [1])],
)
def test_training_does_not_invent_grouping_identities(
    frames: list[int], videos: list[int]
) -> None:
    with pytest.raises(ValueError, match="identities"):
        trainer._grouping_ids(["image.jpg"], frames, videos)


@pytest.mark.parametrize(
    "role", ["standalone", "site_node", "local_study_server", "central_hub"]
)
def test_trainer_preserves_frame_identity_in_all_loaders(role: str) -> None:
    paths = [f"frame-{index}.jpg" for index in range(1, 7)]
    labels = [[1, 0]] * 6
    masks = [[1, 1]] * 6
    data = trainer._PreparedTrainingData(
        dataset=AIDataSet(),
        image_paths=paths,
        label_vectors=[[1, 0] for _ in paths],
        label_masks=masks,
        labels=[],
        labelset=LabelSet(),
        frame_ids=list(range(1, 7)),
        video_ids=list(range(1, 7)),
        kept_indices=[0, 1],
        labels_arr=labels,
        masks_arr=masks,
        labels_tensor=torch.tensor(labels, dtype=torch.float32),
        masks_tensor=torch.tensor(masks, dtype=torch.float32),
    )
    with override_settings(ENDOREG_DEPLOYMENT_ROLE=role):
        loaders = trainer._build_training_loaders(
            data, TrainingConfig(dataset_id=1, val_split=0.2, test_split=0.2)
        )
    assert loaders.full_dataset.frame_ids == list(range(1, 7))
    selected: list[int] = []
    for loader in (loaders.train, loaders.validation, loaders.test):
        subset = loader.dataset
        assert isinstance(subset, EndoMultiLabelDataset)
        selected.extend(subset.frame_ids)
    assert sorted(selected) == list(range(1, 7))
    from lx_ai_core.training import TrainingSample

    frames = {
        pk: Frame(pk=pk, video=VideoFile(pk=pk), frame_number=pk * 10, timestamp=pk / 2)
        for pk in data.frame_ids
    }
    with patch.object(Frame.objects, "select_related") as query:
        query.return_value.in_bulk.return_value = frames
        samples = trainer._training_samples(TrainingSample, data)
    assert [sample.frame_stream.frame_number for sample in samples] == [
        pk * 10 for pk in data.frame_ids
    ]
    assert all(sample.path is None for sample in samples)
    assert [sample.timestamp for sample in samples] == [pk / 2 for pk in data.frame_ids]


@pytest.mark.parametrize(
    ("frames", "videos", "validation", "test"),
    [
        ([], [], 0.2, 0.1),
        ([1], [], 0.2, 0.1),
        ([1, 2], [1, 2], 0.4, 0.4),
        ([1, 2], [1, 2], -0.1, 0.2),
        ([1, 2], [1, 2], float("nan"), 0.2),
    ],
)
def test_split_rejects_invalid_or_exhausted_partitions(
    frames: list[int], videos: list[int], validation: float, test: float
) -> None:
    with pytest.raises(ValueError):
        trainer.groupwise_split_indices_by_video(frames, videos, validation, test)

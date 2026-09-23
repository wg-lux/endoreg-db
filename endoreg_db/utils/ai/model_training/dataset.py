# endoreg_db/utils/ai/model_training/dataset.py

from __future__ import annotations

from typing import Optional, Sequence, Tuple, List

import numpy as np
import torch
from torch import Tensor
from torch.utils.data import Dataset


# Dataset ist eine generische Klasse. Wir müssen Pyright mitteilen,
# welchen Typ ein einzelnes Element (__getitem__) zurückgibt.
class EndoMultiLabelDataset(Dataset[Tuple[torch.Tensor, torch.Tensor, torch.Tensor]]):
    """
    PyTorch dataset wrapping the output of build_dataset_for_training.

    Each item is:
        image_tensor: FloatTensor [3, H, W]
        labels:       FloatTensor [num_labels]   (0 or 1; unknown -> 0 but masked)
        mask:         FloatTensor [num_labels]   (1 known, 0 unknown)
    """

    def __init__(
        self,
        image_paths: Sequence[str],
        label_vectors: Sequence[Sequence[Optional[int]]],
        label_masks: Sequence[Sequence[int]],
        image_size: int = 224,
        frame_ids: Sequence[int] | None = None,
    ) -> None:
        if not image_paths or not (
            len(image_paths) == len(label_vectors) == len(label_masks)
        ):
            raise ValueError(
                "image_paths, label_vectors, label_masks must have the same nonzero length"
            )
        if type(image_size) is not int or image_size <= 0:
            raise ValueError("image_size must be a positive integer")

        self.image_paths: List[str] = list(image_paths)
        if frame_ids is None or len(frame_ids) != len(image_paths):
            raise ValueError(
                "Validated frame_ids are required for every training image"
            )
        if any(type(frame_id) is not int or frame_id <= 0 for frame_id in frame_ids):
            raise ValueError("frame_ids must contain positive integer identities")
        self.frame_ids: list[int] = list(frame_ids)

        # Explizite Typisierung der leeren Listen, um "Unknown Member Type"
        # beim anschließenden .append() zu verhindern.
        label_vec_list: List[List[int]] = []
        mask_list: List[List[int]] = []

        label_count = len(label_vectors[0])
        if label_count == 0:
            raise ValueError("Training label vectors must not be empty")
        for vec, mask in zip(label_vectors, label_masks, strict=True):
            if len(vec) != label_count or len(mask) != label_count:
                raise ValueError("Every label vector and mask must have the same width")
            for value, known in zip(vec, mask, strict=True):
                if type(known) not in (int, bool) or known not in (0, 1):
                    raise ValueError("Label masks must contain only binary integers")
                if value is not None and (
                    type(value) not in (int, bool) or value not in (0, 1)
                ):
                    raise ValueError(
                        "Labels must contain binary integers or unknown values"
                    )
                if value is None and known != 0:
                    raise ValueError("Unknown labels must be masked out")
            v = [0 if (x is None) else int(x) for x in vec]
            m = [int(x) for x in mask]
            label_vec_list.append(v)
            mask_list.append(m)

        self.labels = torch.tensor(label_vec_list, dtype=torch.float32)  # [N, C]
        self.masks = torch.tensor(mask_list, dtype=torch.float32)  # [N, C]

        self.num_labels = self.labels.shape[1]
        self.image_size = image_size

        # ImageNet-style normalization
        self.mean = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
        self.std = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)

    def __len__(self) -> int:
        return len(self.image_paths)

    def _load_image(self, frame_id: int) -> torch.Tensor:
        """
        Read approved processed media, resize, normalize to tensor [3, H, W].
        """
        from endoreg_db.models.media.frame.frame import Frame
        from endoreg_db.services.frames.training_images import (
            read_processed_training_image,
        )

        frame = Frame.objects.select_related("video__state").get(pk=frame_id)
        img = read_processed_training_image(frame)
        img = img.resize((self.image_size, self.image_size))
        arr = np.array(img, dtype=np.float32) / 255.0  # [H, W, C]

        tensor = torch.as_tensor(arr).permute(2, 0, 1)  # [C, H, W]
        tensor = (tensor - self.mean) / self.std
        return tensor

    def __getitem__(self, idx: int) -> Tuple[Tensor, Tensor, Tensor]:
        x = self._load_image(self.frame_ids[idx])
        y = self.labels[idx]
        m = self.masks[idx]
        return x, y, m

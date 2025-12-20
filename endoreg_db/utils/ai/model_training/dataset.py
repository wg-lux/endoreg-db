# endoreg_db/utils/ai/model_training/dataset.py

from __future__ import annotations

from typing import Optional, Sequence, Tuple, List

import numpy as np
from PIL import Image
import torch
from torch.utils.data import Dataset


class EndoMultiLabelDataset(Dataset):
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
    ) -> None:
        assert len(image_paths) == len(label_vectors) == len(label_masks), (
            "image_paths, label_vectors, label_masks must have same length"
        )

        self.image_paths: List[str] = list(image_paths)

        # Convert vectors with None → 0, but mask will ensure they are ignored
        label_vec_list = []
        mask_list = []
        for vec, mask in zip(label_vectors, label_masks):
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

    def _load_image(self, path: str) -> torch.Tensor:
        """
        Load image from disk, resize, convert to normalized tensor [3, H, W].
        """
        img = Image.open(path).convert("RGB")
        img = img.resize((self.image_size, self.image_size))
        arr = np.array(img, dtype=np.float32) / 255.0  # [H, W, C]
        tensor = torch.from_numpy(arr).permute(2, 0, 1)  # [C, H, W]
        tensor = (tensor - self.mean) / self.std
        return tensor

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        path = self.image_paths[idx]
        x = self._load_image(path)
        y = self.labels[idx]
        m = self.masks[idx]
        return x, y, m

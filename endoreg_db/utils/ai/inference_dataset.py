# endoreg_db/utils/ai/inference_dataset.py

from torch.utils.data import Dataset
import numpy as np
from PIL import Image
from torchvision import transforms  # type: ignore
from .preprocess import Cropper
import torch
from typing import Any, Dict, Sequence, cast


class InferenceDataset(Dataset[torch.Tensor]):
    def __init__(
        self, paths: Sequence[str], crops: Sequence[Any], config: Dict[str, Any]
    ) -> None:
        self.paths = paths
        self.crops = crops
        self.cropper = Cropper()  # Assuming Cropper can work with NumPy arrays
        self.config = config

        # Initialize the image transformations using torchvision
        self.transforms = transforms.Compose(
            [
                # Convert PIL image to PyTorch tensor
                transforms.ToTensor(),
                # Normalize the image using the provided mean and std
                transforms.Normalize(mean=self.config["mean"], std=self.config["std"]),
            ]
        )

    def __len__(self) -> int:
        # Returns the total number of samples
        return len(self.paths)

    def __getitem__(self, idx: int) -> torch.Tensor:
        # Open the image with Pillow
        with Image.open(self.paths[idx]) as raw_image:
            # Convert the image to RGB to ensure 3 channels
            pil_image = raw_image.convert("RGB")

            # Get the corresponding crop for the current image
            crop = self.crops[idx]

            # FIXED: scale von List [x, y] zu Tuple (x, y) geändert + Typen abgesichert
            # Gesamte Cropper-Logik in den Context-Manager gezogen, um Typ-Leakage zu vermeiden
            cropped = self.cropper(
                np.array(pil_image),  # Convert PIL image to numpy array for cropping
                crop,
                scale=(int(self.config["size_x"]), int(self.config["size_y"])),
            )

        # Convert cropped numpy array back to PIL image for torchvision transforms
        cropped_pil = Image.fromarray(cropped.astype("uint8"))

        # Apply the transformations
        img = cast(Image, self.transforms(cropped_pil))

        return cast(torch.Tensor, img)

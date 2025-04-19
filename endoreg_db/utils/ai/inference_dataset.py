from torch.utils.data import Dataset
import numpy as np
from PIL import Image
from torchvision import transforms
from .preprocess import Cropper

class InferenceDataset(Dataset):
    def __init__(self, paths, crops, config):
        self.paths = paths
        self.crops = crops
        self.cropper = Cropper()  # Assuming Cropper can work with NumPy arrays
        self.config = config

        # Initialize the image transformations using torchvision
        self.transforms = transforms.Compose([
            # Convert PIL image to PyTorch tensor
            transforms.ToTensor(),
            # Normalize the image using the provided mean and std
            transforms.Normalize(mean=self.config["mean"], std=self.config["std"])
        ])
        
    def __len__(self):
        # Returns the total number of samples
        return len(self.paths)

    def __getitem__(self, idx):
        # Open the image with Pillow
        with Image.open(self.paths[idx]) as pil_image:
            # Convert the image to RGB to ensure 3 channels
            pil_image = pil_image.convert('RGB')

        # Get the corresponding crop for the current image
        crop = self.crops[idx]

        # Crop the image based on the provided crop parameters and convert to numpy for cropping
        cropped = self.cropper(
            np.array(pil_image),  # Convert PIL image to numpy array for cropping
            crop,
            scale=[
                self.config["size_x"],
                self.config["size_y"]
            ]    
        )

        # Convert cropped numpy array back to PIL image for torchvision transforms
        cropped_pil = Image.fromarray(cropped.astype('uint8'), 'RGB')
        
        # Apply the transformations
        img = self.transforms(cropped_pil)


        return img

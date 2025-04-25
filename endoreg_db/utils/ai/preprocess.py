import numpy as np
from PIL import Image, ImageOps  # Import the required modules from Pillow

def crop_img(img, crop):
    """
    Crops the image based on the specified dimensions and adds padding to maintain aspect ratio.

    Parameters:
        img: PIL Image object.
        crop: Tuple of (ymin, ymax, xmin, xmax) specifying the crop area.

    Returns:
        PIL Image object that has been cropped and padded as necessary.
    """
    # Convert crop dimensions to Pillow format: left, upper, right, lower
    ymin, ymax, xmin, xmax = crop
    img_cropped = img.crop((xmin, ymin, xmax, ymax))
    
    # Calculate the new size and the required padding
    width, height = img_cropped.size
    delta = width - height
    
    if delta > 0:
        padding = (0, abs(delta) // 2, 0, abs(delta) - abs(delta) // 2)  # (left, top, right, bottom)
    elif delta < 0:
        padding = (abs(delta) // 2, 0, abs(delta) - abs(delta) // 2, 0)
    else:
        padding = (0, 0, 0, 0)
    
    # Pad the image to make it square
    img_padded = ImageOps.expand(img_cropped, padding)
    
    return img_padded


class Cropper:
    def __init__(self):
        pass

    def __call__(self, img, crop=None, scale=None, scale_method=Image.Resampling.LANCZOS):
        """
        Applies cropping and scaling transformations to the input image.

        Parameters:
            img: PIL Image object or numpy array of the image.
            crop: Optional tuple specifying the cropping area (y_min, y_max, x_min, x_max).
            scale: Optional tuple specifying the new size (width, height).
            scale_method: Resampling method used for scaling (default is Image.Resampling.LANCZOS).

        Returns:
            Numpy array of the processed image.
        """
        # Convert numpy array to PIL Image if necessary
        if isinstance(img, np.ndarray):
            img = Image.fromarray(img.astype('uint8'), 'RGB')
        
        if crop is not None:
            img = crop_img(img, crop)
        else:
            raise Exception("Automatic crop detection not implemented yet")
        
        if scale is not None:
            img = img.resize(scale, resample=scale_method)
        
        # Convert PIL Image back to numpy array
        img = np.array(img)
        
        return img

import huggingface_hub
from typing import Optional

def download_segmentation_model(
    repo_id: str = "wg-lux/colo_segmentation_RegNetX800MF_base",
    filename: str = "model.safetensors",
    cache_dir: Optional[str] = None
) -> str:
    """
    Downloads a segmentation model from Hugging Face and caches it locally.

    Args:
        repo_id (str): The Hugging Face repository ID (default: wg-lux model).
        filename (str): The specific file to download from the repo (default: model.safetensors).
        cache_dir (str): The directory to cache the downloaded model. If None, uses HF default cache.

    Returns:
        str: The local path to the downloaded model.
        
    Example:
        >>> model_path = download_segmentation_model()
        >>> # Downloads from wg-lux/colo_segmentation_RegNetX800MF_base
    """
    local_path = huggingface_hub.hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        cache_dir=cache_dir,
        force_download=False,
        resume_download=True,
    )
    return local_path
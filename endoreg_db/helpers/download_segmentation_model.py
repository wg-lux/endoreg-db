from __future__ import annotations

from pathlib import Path
from types import NoneType
from typing import Protocol, cast

import huggingface_hub

type Null = NoneType


class _HfHubDownload(Protocol):
    def __call__(
        self,
        repo_id: str,
        filename: str,
        *,
        cache_dir: str | Path | Null = None,
        force_download: bool = False,
        resume_download: bool = True,
    ) -> str: ...


def download_segmentation_model(
    repo_id: str = "wg-lux/colo_segmentation_RegNetX800MF_base",
    filename: str = "model.safetensors",
    cache_dir: str | Path | Null = None,
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
    hf_hub_download = cast(_HfHubDownload, getattr(huggingface_hub, "hf_hub_download"))
    local_path = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        cache_dir=cache_dir,
        force_download=False,
        resume_download=True,
    )
    return local_path

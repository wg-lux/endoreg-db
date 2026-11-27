"""Host persistence for immutable masks validated and loaded by lx-dtypes."""

import yaml

from lx_dtypes.models.contracts.video_masks import VideoMaskProfile
from lx_dtypes.models.interface.video_masks import load_video_mask, load_video_masks

from endoreg_db.utils.file_operations import atomic_create_file
from endoreg_db.utils.paths import get_runtime_paths


def list_video_masks() -> list[VideoMaskProfile]:
    return list(
        load_video_masks(get_runtime_paths().terminology / "video_masks").values()
    )


def get_video_mask(name: str) -> VideoMaskProfile:
    return load_video_mask(name, get_runtime_paths().terminology / "video_masks")


def create_video_mask(profile: VideoMaskProfile) -> VideoMaskProfile:
    if any(item.name == profile.name for item in list_video_masks()):
        raise FileExistsError("A mask with this name already exists")
    content = yaml.safe_dump(profile.model_dump(mode="json"), sort_keys=False).encode()
    atomic_create_file(
        destination=get_runtime_paths().terminology
        / "video_masks"
        / f"{profile.name}.yml",
        content=[content],
        required_bytes=len(content),
        file_mode=0o600,
        dir_mode=0o700,
    )
    return profile

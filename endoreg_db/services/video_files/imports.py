from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

from endoreg_db.services.streamable_media import sync_video_streamable_artifacts

from .frames import initialize_video_frames
from .io import set_video_frame_dir
from .metadata import initialize_video_specs, update_video_meta
from .state import get_or_create_video_state

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)


def _video_file_model():
    from endoreg_db.models.media.video.video_file import VideoFile

    return VideoFile


def create_video_file_from_path(
    file_path: Union[str, Path],
    center_name: str,
    *,
    model_cls: type["VideoFile"] | None = None,
    **kwargs,
) -> Optional["VideoFile"]:
    from endoreg_db.utils.security.hashs import get_video_hash

    from ._imports import _create_from_file

    if isinstance(file_path, str):
        file_path = Path(file_path)
    if not center_name:
        try:
            center_name = os.environ["CENTER_NAME"]
        except KeyError:
            logger.error(
                "Center name must be provided to create VideoFile from file. "
                "You can set CENTER_NAME in environment variables."
            )
            return None

    processor_name = kwargs.pop("processor_name", None)
    video_hash = kwargs.pop("video_hash", None)
    if not video_hash:
        video_hash = str(get_video_hash(file_path))

    return _create_from_file(
        model_cls or _video_file_model(),
        file_path,
        center_name=center_name,
        processor_name=processor_name,
        video_hash=video_hash,
        **kwargs,
    )


def create_initialized_video_file_from_path(
    file_path: Union[str, Path],
    center_name: str,
    processor_name: Optional[str],
    video_hash: str,
    *,
    save_video_file: bool = True,
    model_cls: type["VideoFile"] | None = None,
) -> "VideoFile":
    from ._imports import _create_from_file

    if isinstance(file_path, str):
        file_path = Path(file_path)

    video_file = _create_from_file(
        cls_model=model_cls or _video_file_model(),
        file_path=file_path,
        center_name=center_name,
        processor_name=processor_name,
        video_hash=video_hash,
        save=save_video_file,
    )
    return initialize_video_file(video_file)


def initialize_video_file(video: "VideoFile") -> "VideoFile":
    update_video_meta(video, save_instance=False)
    try:
        if video.has_raw and (
            video.fps is None
            or video.width is None
            or video.height is None
            or video.frame_count is None
            or video.duration is None
        ):
            initialize_video_specs(video, use_raw=True)
        else:
            logger.debug(
                "Skipping OpenCV video spec init for %s; specs already available or raw file missing.",
                video.video_hash,
            )
    except Exception as exc:
        logger.error(
            "Failed to initialize video specs for %s: %s", video.video_hash, exc
        )

    set_video_frame_dir(video)
    video.state = get_or_create_video_state(video)
    video.save(
        update_fields=[
            "video_meta",
            "fps",
            "duration",
            "frame_count",
            "width",
            "height",
            "state",
        ]
    )
    try:
        sync_video_streamable_artifacts(
            video,
            include_raw=True,
            include_processed=False,
            save=True,
        )
    except Exception as exc:
        logger.warning(
            "Could not synchronize initial streamable artifact state for video %s: %s",
            video.pk,
            exc,
        )

    initialize_video_frames(video)
    return video

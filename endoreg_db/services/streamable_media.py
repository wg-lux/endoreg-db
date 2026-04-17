from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from endoreg_db.utils.paths import (
    protected_media_root,
    to_protected_media_relative,
    to_storage_relative,
)
from endoreg_db.utils.file_operations import (
    atomic_write_file,
)
from endoreg_db.utils.storage_profile import (
    PayloadKind,
    StoragePolicy,
    resolve_storage_policy,
)
from endoreg_db.utils.storage_streaming import field_file_size, iter_field_file_bytes

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile

STREAMABLE_VIDEO_ROOT = Path(
    os.environ.get(
        "LX_ANNOTATE_STREAMABLE_VIDEO_ROOT",
        str(protected_media_root() / "streamable_videos"),
    )
).resolve()
STREAMABLE_RAW_VIDEO_ROOT = Path(
    os.environ.get(
        "LX_ANNOTATE_STREAMABLE_VIDEO_RAW_ROOT",
        str(STREAMABLE_VIDEO_ROOT / "raw"),
    )
).resolve()
STREAMABLE_PROCESSED_VIDEO_ROOT = Path(
    os.environ.get(
        "LX_ANNOTATE_STREAMABLE_VIDEO_PROCESSED_ROOT",
        str(STREAMABLE_VIDEO_ROOT / "processed"),
    )
).resolve()
STREAMABLE_DIRECTORY_MODE = 0o750
STREAMABLE_FILE_MODE = 0o640


def _streamable_relative_path(target_path: Path) -> str:
    resolved_target = Path(target_path).resolve()
    try:
        return to_protected_media_relative(resolved_target)
    except ValueError:
        return to_storage_relative(resolved_target)


def _materialize_streamable_target(video_field_file, target_path: Path) -> Path:
    file_size = field_file_size(video_field_file)
    return atomic_write_file(
        destination=target_path,
        content=iter_field_file_bytes(
            video_field_file,
            start=0,
            end=file_size - 1,
        ),
        required_bytes=file_size,
        file_mode=STREAMABLE_FILE_MODE,
        dir_mode=STREAMABLE_DIRECTORY_MODE,
    )


def _video_streamable_target(
    video: "VideoFile", *, processed: bool, suffix: str
) -> Path:
    stem = (
        getattr(video, "processed_video_hash", None) or video.video_hash
        if processed
        else video.video_hash
    )
    root = STREAMABLE_PROCESSED_VIDEO_ROOT if processed else STREAMABLE_RAW_VIDEO_ROOT
    return root / f"{stem}{suffix}"


def sync_video_streamable_artifacts(
    video: "VideoFile",
    *,
    include_raw: bool = True,
    include_processed: bool = True,
    save: bool = True,
) -> list[str]:
    update_fields: list[str] = []
    synced_any = False
    raw_storage_policy = resolve_storage_policy(PayloadKind.VIDEO_RAW)
    processed_storage_policy = resolve_storage_policy(PayloadKind.VIDEO_PROCESSED)

    if (
        raw_storage_policy == StoragePolicy.FS_STREAMABLE
        and include_raw
        and getattr(video, "raw_file", None)
        and getattr(video.raw_file, "name", None)
    ):
        target_path = _video_streamable_target(
            video,
            processed=False,
            suffix=Path(video.raw_file.name).suffix or ".mp4",
        )
        relative_path = _streamable_relative_path(
            _materialize_streamable_target(video.raw_file, target_path)
        )
        if video.streamable_relative_path != relative_path:
            video.streamable_relative_path = relative_path
            update_fields.append("streamable_relative_path")
        synced_any = True
    elif (
        include_raw
        and getattr(video, "raw_file", None)
        and getattr(video.raw_file, "name", None)
    ):
        logger.info(
            "Skipping raw streamable artifact sync for video %s because "
            "ENDOREG_STORAGE_PROFILE routes raw video to %s.",
            video.pk,
            raw_storage_policy,
        )
        if video.streamable_relative_path:
            video.streamable_relative_path = ""
            update_fields.append("streamable_relative_path")

    if (
        processed_storage_policy == StoragePolicy.FS_STREAMABLE
        and include_processed
        and getattr(video, "processed_file", None)
        and getattr(video.processed_file, "name", None)
    ):
        target_path = _video_streamable_target(
            video,
            processed=True,
            suffix=Path(video.processed_file.name).suffix or ".mp4",
        )
        relative_path = _streamable_relative_path(
            _materialize_streamable_target(video.processed_file, target_path)
        )
        if video.processed_streamable_relative_path != relative_path:
            video.processed_streamable_relative_path = relative_path
            update_fields.append("processed_streamable_relative_path")
        synced_any = True
    elif (
        include_processed
        and getattr(video, "processed_file", None)
        and getattr(video.processed_file, "name", None)
    ):
        logger.info(
            "Skipping processed streamable artifact sync for video %s because "
            "ENDOREG_STORAGE_PROFILE routes processed video to %s.",
            video.pk,
            processed_storage_policy,
        )
        if video.processed_streamable_relative_path:
            video.processed_streamable_relative_path = ""
            update_fields.append("processed_streamable_relative_path")

    storage_mode_cls = type(video).StorageMode
    preferred_storage_mode = (
        storage_mode_cls.FS_ENCRYPTED_STREAMABLE
        if StoragePolicy.FS_STREAMABLE in {raw_storage_policy, processed_storage_policy}
        else storage_mode_cls.APP_ENCRYPTED
    )
    if video.storage_mode != preferred_storage_mode:
        video.storage_mode = preferred_storage_mode
        update_fields.append("storage_mode")
    if not synced_any and preferred_storage_mode == storage_mode_cls.APP_ENCRYPTED:
        if video.streamable_relative_path:
            video.streamable_relative_path = ""
            update_fields.append("streamable_relative_path")
        if video.processed_streamable_relative_path:
            video.processed_streamable_relative_path = ""
            update_fields.append("processed_streamable_relative_path")

    if save and update_fields:
        video.save(update_fields=list(dict.fromkeys(update_fields)))
        logger.info(
            "Synchronized streamable video artifacts for %s (%s)",
            video.pk,
            ", ".join(dict.fromkeys(update_fields)),
        )

    return list(dict.fromkeys(update_fields))

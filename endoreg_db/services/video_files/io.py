# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportUnusedClass=false
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def ensure_local_raw_video_file(video: "VideoFile"):
    from ._io import _ensure_local_raw_file

    return _ensure_local_raw_file(video)


def ensure_local_processed_video_file(video: "VideoFile"):
    from ._io import _ensure_local_processed_file

    return _ensure_local_processed_file(video)


def delete_video_with_owned_files(
    video: "VideoFile",
    using: str | None = None,
    keep_parents: bool = False,
) -> tuple[int, dict[str, int]]:
    from ._io import _delete_with_file

    return _delete_with_file(video, using=using, keep_parents=keep_parents)


def get_video_base_frame_dir(video: "VideoFile") -> Path:
    from ._io import _get_base_frame_dir

    return _get_base_frame_dir(video)


def set_video_frame_dir(video: "VideoFile", force_update: bool = False):
    from ._io import _set_frame_dir

    return _set_frame_dir(video, force_update=force_update)


def get_video_frame_dir_path(video: "VideoFile") -> Optional[Path]:
    from ._io import _get_frame_dir_path

    return _get_frame_dir_path(video)


def get_temp_anonymized_video_frame_dir(video: "VideoFile") -> Path:
    from ._io import _get_temp_anonymized_frame_dir

    return _get_temp_anonymized_frame_dir(video)


def get_target_anonymized_video_path(video: "VideoFile") -> Path:
    from ._io import _get_target_anonymized_video_path

    return _get_target_anonymized_video_path(video)


def get_raw_video_file_path(video: "VideoFile") -> Optional[Path]:
    from ._io import _get_raw_file_path

    return _get_raw_file_path(video)


def get_processed_video_file_path(video: "VideoFile") -> Optional[Path]:
    from ._io import _get_processed_file_path

    return _get_processed_file_path(video)


def get_raw_video_stream_path(video: "VideoFile") -> Optional[Path]:
    from ._io import _get_raw_stream_path

    return _get_raw_stream_path(video)


def get_processed_video_stream_path(
    video: "VideoFile",
    *,
    materialize_if_missing: bool = False,
) -> Optional[Path]:
    from ._io import _get_processed_stream_path

    return _get_processed_stream_path(
        video,
        materialize_if_missing=materialize_if_missing,
    )

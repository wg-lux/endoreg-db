from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def anonymize_video_file(video: "VideoFile", delete_original_raw: bool = True) -> bool:
    from endoreg_db.models.media.video.video_file_anonymize import _anonymize

    return _anonymize(video, delete_original_raw=delete_original_raw)


def create_anonymized_video_frame_files(video: "VideoFile", *args, **kwargs):
    from endoreg_db.models.media.video.video_file_anonymize import (
        _create_anonymized_frame_files,
    )

    return _create_anonymized_frame_files(video, *args, **kwargs)


def cleanup_video_raw_assets(
    video_hash: str,
    *,
    raw_file_name: str = "",
    raw_file_path: Path | None = None,
    raw_frame_dir: Path | None = None,
) -> None:
    from endoreg_db.models.media.video.video_file_anonymize import _cleanup_raw_assets

    _cleanup_raw_assets(
        video_hash=video_hash,
        raw_file_name=raw_file_name,
        raw_file_path=raw_file_path,
        raw_frame_dir=raw_frame_dir,
    )


def merge_outside_frame_intervals(
    video: "VideoFile",
    *,
    only_validated: bool = False,
) -> list[tuple[int, int]]:
    from endoreg_db.services.video_post_validation_blackening import (
        merge_outside_frame_intervals as _merge_outside_frame_intervals,
    )

    return _merge_outside_frame_intervals(video, only_validated=only_validated)


def rebuild_processed_video_without_outside_frames(
    video: "VideoFile",
    *,
    only_validated: bool = False,
    outside_intervals: Sequence[tuple[int, int]] | None = None,
) -> bool:
    from endoreg_db.services.video_post_validation_blackening import (
        rebuild_processed_video_without_outside_frames as _rebuild,
    )

    return _rebuild(
        video,
        only_validated=only_validated,
        outside_intervals=outside_intervals,
    )

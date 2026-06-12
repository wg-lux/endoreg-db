# pyright: reportPrivateUsage=false
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lx_dtypes.models.contracts.video_text_metadata import (
    VideoTextMetaPayload,
)
from lx_dtypes.models.contracts import RoiBoxCore

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
    from endoreg_db.models.metadata.video_meta import FFMpegMeta


def get_video_ffmpeg_meta(video: "VideoFile") -> "FFMpegMeta":
    from endoreg_db.models.metadata.video_meta import FFMpegMeta

    if video.video_meta is not None:
        if video.video_meta.ffmpeg_meta is not None:
            return video.video_meta.ffmpeg_meta
        raise AssertionError("Expected FFMpegMeta instance.")

    initialize_video_specs(video)
    ffmpeg_meta = video.video_meta.ffmpeg_meta if video.video_meta else None
    assert isinstance(ffmpeg_meta, FFMpegMeta), "Expected FFMpegMeta instance."
    return ffmpeg_meta


def update_video_meta(
    video: "VideoFile",
    save_instance: bool = True,
    raw_video_path: Path | None = None,
) -> "VideoFile | None":
    from ._metadata import _update_video_meta

    return _update_video_meta(
        video,
        save_instance=save_instance,
        raw_video_path=raw_video_path,
    )


def initialize_video_specs(
    video: "VideoFile", use_raw: bool = True, local_video_path: Path | None = None
) -> bool:
    from ._metadata import _initialize_video_specs

    return _initialize_video_specs(
        video, use_raw=use_raw, local_video_path=local_video_path
    )


def get_video_fps(video: "VideoFile") -> float:
    from ._metadata import _get_fps

    return _get_fps(video)


def get_video_endo_roi(video: "VideoFile") -> RoiBoxCore | None:
    from ._metadata import get_endo_roi

    return get_endo_roi(video)


def get_video_crop_template(video: "VideoFile") -> list[int] | None:
    from ._metadata import _get_crop_template

    return _get_crop_template(video)


def get_video_import_processor(video: "VideoFile"):
    from ._metadata import _get_import_processor

    return _get_import_processor(video)


def get_video_import_context_names(video: "VideoFile") -> tuple[str, str]:
    from ._metadata import _get_import_context_names

    return _get_import_context_names(video)


def update_video_text_metadata(
    video: "VideoFile",
    extracted_data_dict: VideoTextMetaPayload | None = None,
    ocr_frame_fraction: float = 0.01,
    cap: int = 10,
    overwrite: bool = False,
) -> "SensitiveMeta | None":
    from ._metadata import _update_text_metadata

    return _update_text_metadata(
        video,
        extracted_data_dict=extracted_data_dict,
        ocr_frame_fraction=ocr_frame_fraction,
        cap=cap,
        overwrite=overwrite,
    )


def ensure_default_video_fps(video: "VideoFile") -> float:
    from ._time import _ensure_default_fps

    return _ensure_default_fps(video)


def get_video_duration(video: "VideoFile") -> float:
    from endoreg_db.utils.calc_duration_seconds import _calc_duration_vf

    return _calc_duration_vf(video)


def video_frame_number_to_seconds(video: "VideoFile", frame_number: int) -> float:
    from ._time import _frame_number_to_s

    return _frame_number_to_s(video, frame_number)

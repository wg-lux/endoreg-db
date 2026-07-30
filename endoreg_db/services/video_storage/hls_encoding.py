from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum

from endoreg_db.config.env import get_hls_encoding_profile_name
from endoreg_db.services.video_storage.contracts import (
    VideoStorageNormalizationError,
)

CUDA_VISIBLE_DEVICES_ENV = "CUDA_VISIBLE_DEVICES"
_CUDA_DEVICE_SELECTOR_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")


class HlsEncoderBackend(StrEnum):
    LIBX264 = "libx264"
    NVIDIA_NVENC = "nvidia_nvenc"


class HlsEncodingProfileName(StrEnum):
    CLINICAL_H264_LIBX264_CRF_V1 = "clinical_h264_libx264_crf_v1"
    CLINICAL_H264_NVENC_CQ_V1 = "clinical_h264_nvenc_cq_v1"


@dataclass(frozen=True, slots=True)
class HlsEncodingProfile:
    name: HlsEncodingProfileName
    encoder: HlsEncoderBackend
    preset: str
    quality: int
    logical_gpu_index: int | None = None

    def __post_init__(self) -> None:
        if not self.preset.strip():
            raise ValueError("HLS encoder preset must not be empty")
        if self.quality < 0 or self.quality > 51:
            raise ValueError("HLS encoder quality must be between 0 and 51")
        if self.encoder == HlsEncoderBackend.LIBX264:
            if self.logical_gpu_index is not None:
                raise ValueError("CPU HLS profiles cannot select a GPU")
            return
        if self.encoder == HlsEncoderBackend.NVIDIA_NVENC:
            if self.logical_gpu_index != 0:
                raise ValueError(
                    "NVENC HLS profiles require isolated logical GPU index 0"
                )
            return
        raise AssertionError(f"Unhandled HLS encoder backend: {self.encoder!r}")

    def ffmpeg_encoder_args(self, *, cpu_threads: int) -> list[str]:
        if cpu_threads < 1:
            raise ValueError("CPU thread count must be positive")
        if self.encoder == HlsEncoderBackend.LIBX264:
            return [
                "-codec:v",
                "libx264",
                "-threads",
                str(cpu_threads),
                "-preset",
                self.preset,
                "-crf",
                str(self.quality),
            ]
        if self.encoder == HlsEncoderBackend.NVIDIA_NVENC:
            return [
                "-codec:v",
                "h264_nvenc",
                "-gpu",
                "0",
                "-preset",
                self.preset,
                "-rc:v",
                "vbr",
                "-cq:v",
                str(self.quality),
                "-b:v",
                "0",
            ]
        raise AssertionError(f"Unhandled HLS encoder backend: {self.encoder!r}")


_HLS_ENCODING_PROFILES = {
    HlsEncodingProfileName.CLINICAL_H264_LIBX264_CRF_V1: HlsEncodingProfile(
        name=HlsEncodingProfileName.CLINICAL_H264_LIBX264_CRF_V1,
        encoder=HlsEncoderBackend.LIBX264,
        preset="medium",
        quality=18,
    ),
    HlsEncodingProfileName.CLINICAL_H264_NVENC_CQ_V1: HlsEncodingProfile(
        name=HlsEncodingProfileName.CLINICAL_H264_NVENC_CQ_V1,
        encoder=HlsEncoderBackend.NVIDIA_NVENC,
        preset="p6",
        quality=18,
        logical_gpu_index=0,
    ),
}


def hls_encoding_profile_by_name(value: object) -> HlsEncodingProfile:
    try:
        name = HlsEncodingProfileName(str(value))
    except ValueError as exc:
        allowed = ", ".join(profile.value for profile in HlsEncodingProfileName)
        raise ValueError(
            f"Unsupported HLS encoding profile {value!r}; expected one of: {allowed}"
        ) from exc
    return _HLS_ENCODING_PROFILES[name]


def configured_hls_encoding_profile() -> HlsEncodingProfile:
    return hls_encoding_profile_by_name(get_hls_encoding_profile_name())


def _isolated_cuda_device_selector() -> str:
    raw_value = os.environ.get(CUDA_VISIBLE_DEVICES_ENV)
    if raw_value is None:
        raise VideoStorageNormalizationError(
            "NVENC HLS requires CUDA_VISIBLE_DEVICES to expose exactly one GPU"
        )
    selector = raw_value.strip()
    if (
        not selector
        or selector in {"-1", "all", "none", "void"}
        or "," in selector
        or _CUDA_DEVICE_SELECTOR_PATTERN.fullmatch(selector) is None
    ):
        raise VideoStorageNormalizationError(
            "NVENC HLS requires CUDA_VISIBLE_DEVICES to contain exactly one "
            "explicit GPU selector"
        )
    return selector


def assert_hls_encoder_runtime_available(
    *,
    ffmpeg_executable: str,
    profile: HlsEncodingProfile,
) -> None:
    if profile.encoder == HlsEncoderBackend.LIBX264:
        return
    if profile.encoder != HlsEncoderBackend.NVIDIA_NVENC:
        raise AssertionError(f"Unhandled HLS encoder backend: {profile.encoder!r}")

    _isolated_cuda_device_selector()
    command = [
        ffmpeg_executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=64x64:r=1",
        "-frames:v",
        "1",
        *profile.ffmpeg_encoder_args(cpu_threads=1),
        "-an",
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VideoStorageNormalizationError(
            "NVENC HLS encoder preflight could not run"
        ) from exc
    if completed.returncode != 0:
        stderr_tail = completed.stderr[-2000:].strip()
        raise VideoStorageNormalizationError(
            "NVENC HLS encoder preflight failed for the isolated GPU: "
            f"returncode={completed.returncode} stderr={stderr_tail}"
        )


__all__ = [
    "CUDA_VISIBLE_DEVICES_ENV",
    "HlsEncoderBackend",
    "HlsEncodingProfile",
    "HlsEncodingProfileName",
    "assert_hls_encoder_runtime_available",
    "configured_hls_encoding_profile",
    "hls_encoding_profile_by_name",
]

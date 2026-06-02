from __future__ import annotations

from enum import StrEnum

from endoreg_db.utils.storage.profile import (
    PayloadKind,
    StoragePolicy,
    resolve_storage_policy,
)


class VideoStorageMode(StrEnum):
    ENCRYPTED = "app_encrypted"
    STREAMABLE = "fs_encrypted_streamable"


VIDEO_STORAGE_MODE_CHOICES: list[tuple[str, str]] = [
    (VideoStorageMode.ENCRYPTED.value, "Application Encrypted"),
    (VideoStorageMode.STREAMABLE.value, "Filesystem Encrypted Streamable"),
]


def coerce_video_storage_mode(value: str | VideoStorageMode | None) -> VideoStorageMode:
    if isinstance(value, VideoStorageMode):
        return value
    if value in {None, ""}:
        return VideoStorageMode(get_default_video_storage_mode_value())
    return VideoStorageMode(str(value))


def get_default_video_storage_mode_value() -> str:
    policy = resolve_storage_policy(PayloadKind.VIDEO_RAW)
    if policy == StoragePolicy.FS_STREAMABLE:
        return VideoStorageMode.STREAMABLE.value
    return VideoStorageMode.ENCRYPTED.value

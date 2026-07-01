from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from endoreg_db.utils.storage_profile import PayloadKind, StoragePolicy

STREAMABLE_DIRECTORY_MODE = 0o750
STREAMABLE_FILE_MODE = 0o640
MP4_SUFFIX = ".mp4"


class StreamableArtifactKind(StrEnum):
    RAW = "raw"
    PROCESSED = "processed"


class StreamableArtifactDisposition(StrEnum):
    SYNC = "sync"
    CLEAR_STALE_PATH = "clear_stale_path"
    IGNORE = "ignore"


@dataclass(frozen=True, slots=True)
class StreamableArtifactSpec:
    kind: StreamableArtifactKind
    payload_kind: PayloadKind
    file_attr: str
    hash_attr: str
    relative_path_attr: str


@dataclass(frozen=True, slots=True)
class StreamableArtifactDecision:
    spec: StreamableArtifactSpec
    include: bool
    storage_policy: StoragePolicy
    disposition: StreamableArtifactDisposition
    field_file: Any | None
    field_file_name: str
    current_relative_path: str
    expected_hash: str
    target_path: Path | None


@dataclass(frozen=True, slots=True)
class StreamableMediaState:
    artifacts: tuple[StreamableArtifactDecision, ...]

    @property
    def has_streamable_policy(self) -> bool:
        return any(
            artifact.storage_policy == StoragePolicy.FS_STREAMABLE
            for artifact in self.artifacts
        )


@dataclass(frozen=True, slots=True)
class StreamableTranscodeProfile:
    height_px: int = 240
    codec: str = "libx264"
    crf: int = 35
    preset: str = "veryfast"
    audio_codec: str = "aac"
    audio_bitrate: str = "32k"
    disable_audio: bool = True
    movflags: str = "+faststart"

    def extra_args(self) -> list[str]:
        args = [
            "-vf",
            f"scale=-2:{self.height_px},format=yuv420p",
            "-movflags",
            self.movflags,
        ]
        if self.disable_audio:
            args.append("-an")
        return args


DEFAULT_STREAMABLE_TRANSCODE_PROFILE = StreamableTranscodeProfile()

STREAMABLE_ARTIFACT_SPECS: tuple[StreamableArtifactSpec, ...] = (
    StreamableArtifactSpec(
        kind=StreamableArtifactKind.RAW,
        payload_kind=PayloadKind.VIDEO_RAW,
        file_attr="raw_file",
        hash_attr="video_hash",
        relative_path_attr="raw_streamable_relative_path",
    ),
    StreamableArtifactSpec(
        kind=StreamableArtifactKind.PROCESSED,
        payload_kind=PayloadKind.VIDEO_PROCESSED,
        file_attr="processed_file",
        hash_attr="processed_video_hash",
        relative_path_attr="processed_streamable_relative_path",
    ),
)

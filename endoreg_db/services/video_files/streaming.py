from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, TypeGuard

from endoreg_db.utils.storage.video_fields import VideoArtifactFieldFile

from endoreg_db.models.media.video.storage_mode import (
    VideoStorageMode,
    coerce_video_storage_mode,
)
from endoreg_db.services.streamable_media import sync_video_streamable_artifacts
from endoreg_db.utils.encryption.encrypted import MAGIC as LX_ENCRYPTED_MAGIC
from endoreg_db.utils.media_urls import build_video_stream_path
from endoreg_db.utils.paths import normalize_protected_media_relative_path
from endoreg_db.utils.rust_backend import is_lx_encrypted_file

from .io import (
    get_processed_video_file_path,
    get_processed_video_stream_path,
    get_raw_video_file_path,
    get_raw_video_stream_path,
)
from .types import VideoArtifactKind

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def _field_has_name(field_file: object) -> TypeGuard[VideoArtifactFieldFile]:
    return isinstance(field_file, VideoArtifactFieldFile) and bool(field_file.name)


def get_active_raw_video_file(video: "VideoFile") -> VideoArtifactFieldFile:
    raw_file = getattr(video, "raw_file", None)
    if _field_has_name(raw_file):
        return raw_file

    raise ValueError(getattr(video, "NO_ACTIVE_FILE", "Has no raw file"))


def get_active_video_file(video: "VideoFile") -> VideoArtifactFieldFile:
    processed_file = getattr(video, "processed_file", None)
    if _field_has_name(processed_file):
        return processed_file

    raw_file = getattr(video, "raw_file", None)
    if _field_has_name(raw_file):
        return raw_file

    raise ValueError(
        "No active file available. VideoFile has neither raw nor processed file."
    )


def get_active_video_file_path(video: "VideoFile") -> Path:
    processed_file = getattr(video, "processed_file", None)
    if _field_has_name(processed_file):
        path = get_processed_video_stream_path(video)
        if path is None:
            path = get_processed_video_file_path(video)
    else:
        path = get_raw_video_stream_path(video)
        if path is None:
            path = get_raw_video_file_path(video)

    if path is None:
        raise ValueError(
            "Active file has no direct filesystem path. Use ensure_local_*_file()."
        )
    return path


def get_protected_video_stream_url(
    video: "VideoFile",
    *,
    artifact_kind: VideoArtifactKind,
) -> str | None:
    if getattr(video, "pk", None) is None:
        return None
    if artifact_kind == VideoArtifactKind.RAW:
        return None
    if artifact_kind == VideoArtifactKind.PROCESSED and not _field_has_name(
        getattr(video, "processed_file", None)
    ):
        return None

    return build_video_stream_path(int(video.pk), file_type="processed")


def get_active_raw_video_file_url(video: "VideoFile") -> str | None:
    return get_protected_video_stream_url(video, artifact_kind=VideoArtifactKind.RAW)


def get_active_video_file_url(video: "VideoFile") -> str | None:
    if _field_has_name(getattr(video, "processed_file", None)):
        processed_url = get_protected_video_stream_url(
            video,
            artifact_kind=VideoArtifactKind.PROCESSED,
        )
        if processed_url is not None:
            return processed_url
    if getattr(video, "pk", None) is None:
        return None
    if _field_has_name(video.raw_file):
        return build_video_stream_path(int(video.pk))
    return None


def get_raw_video_stream_relative_path(video: "VideoFile") -> str | None:
    relative_path = getattr(video, "raw_streamable_relative_path", "")
    try:
        return normalize_protected_media_relative_path(relative_path)
    except ValueError:
        return None


def get_processed_video_stream_relative_path(video: "VideoFile") -> str | None:
    relative_path = getattr(video, "processed_streamable_relative_path", "")
    try:
        return normalize_protected_media_relative_path(relative_path)
    except ValueError:
        return None


def get_video_stream_relative_path(
    video: "VideoFile",
    artifact_kind: VideoArtifactKind,
) -> str | None:
    if artifact_kind == VideoArtifactKind.PROCESSED:
        return get_processed_video_stream_relative_path(video)
    return get_raw_video_stream_relative_path(video)


def _artifact_field_file(
    video: "VideoFile",
    artifact_kind: VideoArtifactKind,
) -> VideoArtifactFieldFile:
    if artifact_kind == VideoArtifactKind.RAW:
        return get_active_raw_video_file(video)
    field_file = getattr(video, "processed_file", None)
    if not _field_has_name(field_file):
        raise FileNotFoundError("No processed file")
    return field_file


def _artifact_stream_path(
    video: "VideoFile",
    artifact_kind: VideoArtifactKind,
) -> Path | None:
    if artifact_kind == VideoArtifactKind.PROCESSED:
        return get_processed_video_stream_path(video)
    return get_raw_video_stream_path(video)


def _materialize_streamable_artifact(
    video: "VideoFile",
    artifact_kind: VideoArtifactKind,
) -> None:
    sync_video_streamable_artifacts(
        video,
        include_raw=artifact_kind == VideoArtifactKind.RAW,
        include_processed=artifact_kind == VideoArtifactKind.PROCESSED,
        save=True,
    )


def _existing_artifact_stream_path(
    video: "VideoFile",
    artifact_kind: VideoArtifactKind,
) -> Path | None:
    stream_path = _artifact_stream_path(video, artifact_kind)
    if stream_path is None or not stream_path.exists():
        return None
    return stream_path


def _resolve_streamable_artifact_path(
    video: "VideoFile",
    artifact_kind: VideoArtifactKind,
    *,
    materialize_if_missing: bool,
) -> Path | None:
    stream_path = _existing_artifact_stream_path(video, artifact_kind)
    if stream_path is not None:
        return stream_path
    if not materialize_if_missing:
        return None
    _materialize_streamable_artifact(video, artifact_kind)
    return _existing_artifact_stream_path(video, artifact_kind)


def _missing_artifact_message(artifact_kind: VideoArtifactKind) -> str:
    return (
        "Processed video file is not available"
        if artifact_kind == VideoArtifactKind.PROCESSED
        else "Raw video file is not available"
    )


def resolve_video_stream_source(
    video: "VideoFile",
    artifact_kind: VideoArtifactKind,
    *,
    materialize_if_missing: bool = False,
) -> tuple[VideoArtifactFieldFile, Path | None]:
    field_file = _artifact_field_file(video, artifact_kind)
    stream_path = _resolve_streamable_artifact_path(
        video,
        artifact_kind,
        materialize_if_missing=materialize_if_missing,
    )
    if stream_path is not None:
        return field_file, stream_path
    local_path = field_file.local_plaintext_path()
    if local_path is not None:
        return field_file, local_path
    if field_file.exists():
        return field_file, None
    raise FileNotFoundError(_missing_artifact_message(artifact_kind))


def can_offload_video_stream(
    video: "VideoFile",
    artifact_kind: VideoArtifactKind,
) -> bool:
    try:
        storage_mode = coerce_video_storage_mode(getattr(video, "storage_mode", None))
    except ValueError:
        return False

    if storage_mode != VideoStorageMode.STREAMABLE:
        return False

    stream_path = _artifact_stream_path(video, artifact_kind)
    if stream_path is None or not stream_path.exists():
        return False

    if is_encrypted_streamable_video_path(stream_path):
        return False

    return True


def is_encrypted_streamable_video_path(path: Path | None) -> bool:
    if path is None:
        return False
    rust_result = is_lx_encrypted_file(path)
    if rust_result is not None:
        return rust_result
    try:
        with path.open("rb") as handle:
            return handle.read(len(LX_ENCRYPTED_MAGIC)) == LX_ENCRYPTED_MAGIC
    except OSError:
        return False

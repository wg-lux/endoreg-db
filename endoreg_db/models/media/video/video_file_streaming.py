from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from django.db.models.fields.files import FieldFile
from django.urls import reverse

from endoreg_db.models.media.video.storage_mode import (
    VideoStorageMode,
    coerce_video_storage_mode,
)
from endoreg_db.services.streamable_media import sync_video_streamable_artifacts
from endoreg_db.utils.encryption.encrypted import MAGIC as LX_ENCRYPTED_MAGIC
from endoreg_db.utils.paths import normalize_protected_media_relative_path
from endoreg_db.utils.storage import file_exists
from endoreg_db.utils.storage_streaming import maybe_local_plaintext_path

if TYPE_CHECKING:
    from .video_file import VideoFile


def _active_raw_file(video: "VideoFile") -> FieldFile:
    """Return the raw file if available, otherwise raise ValueError."""
    if video.has_raw:
        return video.raw_file
    raise ValueError(video.NO_ACTIVE_FILE)


def _protected_stream_url(video: "VideoFile", *, file_type: str) -> str | None:
    if video.pk is None:
        return None
    if file_type == "processed" and not video.is_processed:
        return None
    if file_type == "raw" and not video.has_raw:
        return None

    try:
        storage_mode = coerce_video_storage_mode(getattr(video, "storage_mode", None))
    except ValueError:
        return None

    if storage_mode == VideoStorageMode.STREAMABLE:
        relative_path = (
            video.processed_streamable_relative_path
            if file_type == "processed"
            else video.raw_streamable_relative_path
        )
        if not isinstance(relative_path, str) or not relative_path.strip():
            return None

    url = reverse("api:video-stream", kwargs={"pk": video.pk})
    if file_type == "processed":
        return f"{url}?type=processed"
    return url


def _active_raw_file_url(video: "VideoFile") -> str | None:
    """Return the URL of the active raw file, or raise ValueError if unavailable."""
    return video._protected_stream_url(file_type="raw")


def _active_file(video: "VideoFile") -> FieldFile:
    """
    Return the active video file, preferring the processed file if available.

    Raises:
        ValueError: If neither a processed nor a raw file is available.
    """
    processed = video.processed_file
    if isinstance(processed, FieldFile) and processed.name:
        return processed

    raw = video.raw_file
    if isinstance(raw, FieldFile) and raw.name:
        return raw

    raise ValueError(
        "No active file available. VideoFile has neither raw nor processed file."
    )


def _active_file_path(video: "VideoFile") -> Path:
    """
    Deprecated: encrypted/storage-backed files may not have a stable local path.

    Use ensure_local_raw_file() / ensure_local_processed_file() for external tools.
    """
    if video.is_processed:
        path = video.get_processed_stream_path()
        if path is None:
            path = video.get_processed_file_path()
    else:
        path = video.get_raw_stream_path()
        if path is None:
            path = video.get_raw_file_path()

    if path is None:
        raise ValueError(
            "Active file has no direct filesystem path. Use ensure_local_*_file()."
        )

    return path


def _active_file_url(video: "VideoFile") -> str | None:
    """Return the URL of the active video file, if available."""
    if video.is_processed:
        processed_url = video._protected_stream_url(file_type="processed")
        if processed_url is not None:
            return processed_url
    return video._protected_stream_url(file_type="raw")


def _get_raw_stream_relative_path(video: "VideoFile") -> str | None:
    relative_path = getattr(video, "raw_streamable_relative_path", "")
    try:
        normalized = normalize_protected_media_relative_path(relative_path)
    except ValueError:
        return None
    return normalized


def _get_processed_stream_relative_path(video: "VideoFile") -> str | None:
    relative_path = getattr(video, "processed_streamable_relative_path", "")
    try:
        normalized = normalize_protected_media_relative_path(relative_path)
    except ValueError:
        return None
    return normalized


def _get_stream_relative_path(video: "VideoFile", file_type: str) -> str | None:
    if file_type == "processed":
        return video.get_processed_stream_relative_path()
    return video.get_raw_stream_relative_path()


def _resolve_video_stream_source(
    video: "VideoFile",
    file_type: str,
    *,
    materialize_if_missing: bool = False,
) -> tuple[FieldFile, Path | None]:
    """
    Returns:
        (field_file, local_path_or_none)

    - field_file is the canonical Django file field for metadata/name/storage access
    - local_path_or_none is a concrete local file path when a streamable artifact or
    local fallback file should be served directly from disk
    """
    if file_type == "processed":
        field_file = getattr(video, "processed_file", None)
        if not field_file or not getattr(field_file, "name", None):
            raise FileNotFoundError("No processed file")

        stream_path = video.get_processed_stream_path()
        if stream_path is not None and stream_path.exists():
            return field_file, stream_path

        if materialize_if_missing:
            sync_video_streamable_artifacts(
                video,
                include_raw=False,
                include_processed=True,
                save=True,
            )
            stream_path = video.get_processed_stream_path()
            if stream_path is not None and stream_path.exists():
                return field_file, stream_path

        local_path = maybe_local_plaintext_path(field_file)
        if local_path is not None:
            return field_file, local_path

        if file_exists(field_file):
            return field_file, None

        raise FileNotFoundError("Processed video file is not available")

    field_file = video.active_raw_file
    if not getattr(field_file, "name", None):
        raise FileNotFoundError("No raw file")

    stream_path = video.get_raw_stream_path()
    if stream_path is not None and stream_path.exists():
        return field_file, stream_path

    if materialize_if_missing:
        sync_video_streamable_artifacts(
            video,
            include_raw=True,
            include_processed=False,
            save=True,
        )
        stream_path = video.get_raw_stream_path()
        if stream_path is not None and stream_path.exists():
            return field_file, stream_path

    local_path = maybe_local_plaintext_path(field_file)
    if local_path is not None:
        return field_file, local_path

    if file_exists(field_file):
        return field_file, None

    raise FileNotFoundError("Raw video file is not available")


def _can_offload_stream_with_nginx(video: "VideoFile", file_type: str) -> bool:
    try:
        storage_mode = coerce_video_storage_mode(getattr(video, "storage_mode", None))
    except ValueError:
        return False

    if storage_mode != VideoStorageMode.STREAMABLE:
        return False

    stream_path = (
        video.get_processed_stream_path()
        if file_type == "processed"
        else video.get_raw_stream_path()
    )

    if stream_path is None or not stream_path.exists():
        return False

    if video._is_encrypted_streamable_path(stream_path):
        return False

    return True


def _is_encrypted_streamable_path(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        with path.open("rb") as handle:
            return handle.read(len(LX_ENCRYPTED_MAGIC)) == LX_ENCRYPTED_MAGIC
    except OSError:
        return False

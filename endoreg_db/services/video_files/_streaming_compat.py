from __future__ import annotations

from pathlib import Path

from django.db.models.fields.files import FieldFile

from .streaming import (
    can_offload_video_stream,
    get_active_raw_video_file,
    get_active_raw_video_file_url,
    get_active_video_file,
    get_active_video_file_path,
    get_active_video_file_url,
    get_processed_video_stream_relative_path,
    get_protected_video_stream_url,
    get_raw_video_stream_relative_path,
    get_video_stream_relative_path,
    is_encrypted_streamable_video_path,
    resolve_video_stream_source,
)
from .types import VideoArtifactKind


def _artifact_kind_from_file_type(file_type: str) -> VideoArtifactKind:
    if file_type == "processed":
        return VideoArtifactKind.PROCESSED
    return VideoArtifactKind.RAW


def _active_raw_file(video) -> FieldFile:
    return get_active_raw_video_file(video)


def _protected_stream_url(video, *, file_type: str) -> str | None:
    return get_protected_video_stream_url(
        video,
        artifact_kind=_artifact_kind_from_file_type(file_type),
    )


def _active_raw_file_url(video) -> str | None:
    return get_active_raw_video_file_url(video)


def _active_file(video) -> FieldFile:
    return get_active_video_file(video)


def _active_file_path(video) -> Path:
    return get_active_video_file_path(video)


def _active_file_url(video) -> str | None:
    return get_active_video_file_url(video)


def _get_raw_stream_relative_path(video) -> str | None:
    return get_raw_video_stream_relative_path(video)


def _get_processed_stream_relative_path(video) -> str | None:
    return get_processed_video_stream_relative_path(video)


def _get_stream_relative_path(video, file_type: str) -> str | None:
    return get_video_stream_relative_path(
        video,
        _artifact_kind_from_file_type(file_type),
    )


def _resolve_video_stream_source(
    video,
    file_type: str,
    *,
    materialize_if_missing: bool = False,
) -> tuple[FieldFile, Path | None]:
    return resolve_video_stream_source(
        video,
        _artifact_kind_from_file_type(file_type),
        materialize_if_missing=materialize_if_missing,
    )


def _can_offload_stream_with_nginx(video, file_type: str) -> bool:
    return can_offload_video_stream(video, _artifact_kind_from_file_type(file_type))


def _is_encrypted_streamable_path(path: Path | None) -> bool:
    return is_encrypted_streamable_video_path(path)


__all__ = [
    "_active_file",
    "_active_file_path",
    "_active_file_url",
    "_active_raw_file",
    "_active_raw_file_url",
    "_can_offload_stream_with_nginx",
    "_get_processed_stream_relative_path",
    "_get_raw_stream_relative_path",
    "_get_stream_relative_path",
    "_is_encrypted_streamable_path",
    "_protected_stream_url",
    "_resolve_video_stream_source",
]

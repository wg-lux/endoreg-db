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
from endoreg_db.utils.storage import file_exists
from endoreg_db.utils.storage.streaming import maybe_local_plaintext_path

from .io import (
    get_processed_video_file_path,
    get_processed_video_stream_path,
    get_raw_video_file_path,
    get_raw_video_stream_path,
)
from .types import VideoArtifactKind

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def _field_has_name(field_file) -> bool:
    return bool(field_file and getattr(field_file, "name", None))


def _field_storage_can_stream(field_file) -> bool:
    storage = getattr(field_file, "storage", None)
    return bool(
        storage is not None
        and (
            hasattr(storage, "iter_decrypted_range")
            or hasattr(storage, "get_plaintext_size")
            or hasattr(storage, "open")
            or hasattr(field_file, "chunks")
        )
    )


def _legacy_fake_local_path(video: "VideoFile", method_name: str) -> Path | None:
    method = vars(video).get(method_name)
    if not callable(method):
        return None
    path = method()
    if path is None:
        return None
    path = Path(path)
    if path.exists():
        return path
    return None


def get_active_raw_video_file(video: "VideoFile") -> FieldFile:
    raw_file = getattr(video, "raw_file", None)
    if _field_has_name(raw_file):
        return raw_file

    fake_raw_file = vars(video).get("active_raw_file")
    if _field_has_name(fake_raw_file):
        return fake_raw_file

    raise ValueError(getattr(video, "NO_ACTIVE_FILE", "Has no raw file"))


def get_active_video_file(video: "VideoFile") -> FieldFile:
    processed_file = getattr(video, "processed_file", None)
    if _field_has_name(processed_file):
        return processed_file

    raw_file = getattr(video, "raw_file", None)
    if _field_has_name(raw_file):
        return raw_file

    fake_raw_file = vars(video).get("active_raw_file")
    if _field_has_name(fake_raw_file):
        return fake_raw_file

    raise ValueError(
        "No active file available. VideoFile has neither raw nor processed file."
    )


def get_active_video_file_path(video: "VideoFile") -> Path:
    processed_file = getattr(video, "processed_file", None)
    if _field_has_name(processed_file):
        path = _legacy_fake_local_path(video, "get_processed_stream_path")
        if path is None:
            path = get_processed_video_stream_path(video)
        if path is None:
            path = get_processed_video_file_path(video)
    else:
        path = _legacy_fake_local_path(video, "get_raw_stream_path")
        if path is None:
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
    if artifact_kind == VideoArtifactKind.PROCESSED and not _field_has_name(
        getattr(video, "processed_file", None)
    ):
        return None
    if artifact_kind == VideoArtifactKind.RAW:
        try:
            get_active_raw_video_file(video)
        except ValueError:
            return None

    try:
        storage_mode = coerce_video_storage_mode(getattr(video, "storage_mode", None))
    except ValueError:
        return None

    if storage_mode == VideoStorageMode.STREAMABLE:
        relative_path = get_video_stream_relative_path(video, artifact_kind)
        if relative_path is None:
            return None

    url = reverse("api:video-stream", kwargs={"pk": video.pk})
    if artifact_kind == VideoArtifactKind.PROCESSED:
        return f"{url}?type=processed"
    return url


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
    return get_protected_video_stream_url(video, artifact_kind=VideoArtifactKind.RAW)


def get_raw_video_stream_relative_path(video: "VideoFile") -> str | None:
    from endoreg_db.models.media.video.video_file_streaming import (
        _get_raw_stream_relative_path,
    )

    return _get_raw_stream_relative_path(video)


def get_processed_video_stream_relative_path(video: "VideoFile") -> str | None:
    from endoreg_db.models.media.video.video_file_streaming import (
        _get_processed_stream_relative_path,
    )

    return _get_processed_stream_relative_path(video)


def get_video_stream_relative_path(
    video: "VideoFile",
    artifact_kind: VideoArtifactKind,
) -> str | None:
    if artifact_kind == VideoArtifactKind.PROCESSED:
        return get_processed_video_stream_relative_path(video)
    return get_raw_video_stream_relative_path(video)


def resolve_video_stream_source(
    video: "VideoFile",
    artifact_kind: VideoArtifactKind,
    *,
    materialize_if_missing: bool = False,
) -> tuple[FieldFile, Path | None]:
    if artifact_kind == VideoArtifactKind.PROCESSED:
        field_file = getattr(video, "processed_file", None)
        if not _field_has_name(field_file):
            raise FileNotFoundError("No processed file")

        stream_path = _legacy_fake_local_path(video, "get_processed_stream_path")
        if stream_path is None:
            stream_path = get_processed_video_stream_path(video)
        if stream_path is not None and stream_path.exists():
            return field_file, stream_path

        if materialize_if_missing:
            sync_video_streamable_artifacts(
                video,
                include_raw=False,
                include_processed=True,
                save=True,
            )
            stream_path = _legacy_fake_local_path(video, "get_processed_stream_path")
            if stream_path is None:
                stream_path = get_processed_video_stream_path(video)
            if stream_path is not None and stream_path.exists():
                return field_file, stream_path

        local_path = maybe_local_plaintext_path(field_file)
        if local_path is not None:
            return field_file, local_path

        direct_path = getattr(field_file, "path", None)
        if direct_path and Path(direct_path).exists():
            return field_file, Path(direct_path)

        fallback_path = _legacy_fake_local_path(video, "get_processed_file_path")
        if fallback_path is not None:
            return field_file, fallback_path

        if file_exists(field_file):
            return field_file, None

        if _field_storage_can_stream(field_file):
            return field_file, None

        raise FileNotFoundError("Processed video file is not available")

    field_file = get_active_raw_video_file(video)
    if not _field_has_name(field_file):
        raise FileNotFoundError("No raw file")

    stream_path = _legacy_fake_local_path(video, "get_raw_stream_path")
    if stream_path is None:
        stream_path = get_raw_video_stream_path(video)
    if stream_path is not None and stream_path.exists():
        return field_file, stream_path

    if materialize_if_missing:
        sync_video_streamable_artifacts(
            video,
            include_raw=True,
            include_processed=False,
            save=True,
        )
        stream_path = _legacy_fake_local_path(video, "get_raw_stream_path")
        if stream_path is None:
            stream_path = get_raw_video_stream_path(video)
        if stream_path is not None and stream_path.exists():
            return field_file, stream_path

    local_path = maybe_local_plaintext_path(field_file)
    if local_path is not None:
        return field_file, local_path

    direct_path = getattr(field_file, "path", None)
    if direct_path and Path(direct_path).exists():
        return field_file, Path(direct_path)

    fallback_path = _legacy_fake_local_path(video, "get_raw_file_path")
    if fallback_path is not None:
        return field_file, fallback_path

    if file_exists(field_file):
        return field_file, None

    if _field_storage_can_stream(field_file):
        return field_file, None

    raise FileNotFoundError("Raw video file is not available")


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

    stream_path = (
        _legacy_fake_local_path(video, "get_processed_stream_path")
        or get_processed_video_stream_path(video)
        if artifact_kind == VideoArtifactKind.PROCESSED
        else _legacy_fake_local_path(video, "get_raw_stream_path")
        or get_raw_video_stream_path(video)
    )
    if stream_path is None or not stream_path.exists():
        return False

    if is_encrypted_streamable_video_path(stream_path):
        return False

    return True


def is_encrypted_streamable_video_path(path: Path | None) -> bool:
    from endoreg_db.models.media.video.video_file_streaming import (
        _is_encrypted_streamable_path,
    )

    return _is_encrypted_streamable_path(path)

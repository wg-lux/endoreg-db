from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from django.db.models.fields.files import FieldFile

from endoreg_db.services.streamable_media_state import build_streamable_media_state
from endoreg_db.services.streamable_media_transcoding import (
    LX_ENCRYPTED_MAGIC,
    is_encrypted_file,
    is_faststart_mp4,
    transcode_streamable_mp4,
)
from endoreg_db.services.streamable_media_types import (
    DEFAULT_STREAMABLE_TRANSCODE_PROFILE,
    MP4_SUFFIX,
    STREAMABLE_ARTIFACT_SPECS,
    STREAMABLE_DIRECTORY_MODE,
    STREAMABLE_FILE_MODE,
    StreamableArtifactDecision,
    StreamableArtifactDisposition,
    StreamableArtifactKind,
    StreamableArtifactSpec,
    StreamableMediaState,
    StreamableTranscodeProfile,
)
from endoreg_db.utils import ffmpeg_wrapper
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.file_operations import (
    atomic_write_file,
    ensure_file_mtime_after,
    safe_unlink_file,
    sha256_file,
)
from endoreg_db.utils.paths import (
    protected_media_root,
    to_protected_media_relative,
    to_storage_relative,
)
from endoreg_db.utils.storage_profile import resolve_storage_policy
from endoreg_db.utils.storage_streaming import field_file_size, iter_field_file_bytes
from endoreg_db.utils.storage_streaming import (
    iter_file_path_bytes,
    local_plaintext_path_from_name,
)

logger = logging.getLogger(__name__)

_is_encrypted_file = is_encrypted_file
_is_faststart_mp4 = is_faststart_mp4
_transcode_streamable_mp4 = transcode_streamable_mp4

__all__ = (
    "DEFAULT_STREAMABLE_TRANSCODE_PROFILE",
    "LX_ENCRYPTED_MAGIC",
    "MP4_SUFFIX",
    "STREAMABLE_ARTIFACT_SPECS",
    "STREAMABLE_DIRECTORY_MODE",
    "STREAMABLE_FILE_MODE",
    "STREAMABLE_PROCESSED_VIDEO_ROOT",
    "STREAMABLE_RAW_VIDEO_ROOT",
    "STREAMABLE_VIDEO_ROOT",
    "StreamableArtifactDecision",
    "StreamableArtifactDisposition",
    "StreamableArtifactKind",
    "StreamableArtifactSpec",
    "StreamableMediaState",
    "StreamableTranscodeProfile",
    "ffmpeg_wrapper",
    "resolve_storage_policy",
    "resolve_streamable_media_state",
    "sync_video_streamable_artifacts",
)

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def _streamable_video_root() -> Path:
    return Path(
        os.environ.get(
            "LX_ANNOTATE_STREAMABLE_VIDEO_ROOT",
            str(protected_media_root() / "streamable_videos"),
        )
    ).resolve()


def _streamable_raw_video_root() -> Path:
    return Path(
        os.environ.get(
            "LX_ANNOTATE_STREAMABLE_VIDEO_RAW_ROOT",
            str(_streamable_video_root() / "raw"),
        )
    ).resolve()


def _streamable_processed_video_root() -> Path:
    return Path(
        os.environ.get(
            "LX_ANNOTATE_STREAMABLE_VIDEO_PROCESSED_ROOT",
            str(_streamable_video_root() / "processed"),
        )
    ).resolve()


STREAMABLE_VIDEO_ROOT = _streamable_video_root()
STREAMABLE_RAW_VIDEO_ROOT = _streamable_raw_video_root()
STREAMABLE_PROCESSED_VIDEO_ROOT = _streamable_processed_video_root()
_DEFAULT_STREAMABLE_RAW_VIDEO_ROOT = STREAMABLE_RAW_VIDEO_ROOT
_DEFAULT_STREAMABLE_PROCESSED_VIDEO_ROOT = STREAMABLE_PROCESSED_VIDEO_ROOT
_DEFAULT_STREAMABLE_RAW_VIDEO_ROOT_FN = _streamable_raw_video_root
_DEFAULT_STREAMABLE_PROCESSED_VIDEO_ROOT_FN = _streamable_processed_video_root


def _streamable_relative_path(target_path: Path) -> str:
    resolved_target = Path(target_path).resolve()

    storage_roots = (
        Path(path_utils.STORAGE_DIR).resolve(),
        path_utils.EndoregPathsModel.from_environment().storage.resolve(),
    )
    for storage_root in dict.fromkeys(storage_roots):
        try:
            return resolved_target.relative_to(storage_root).as_posix()
        except ValueError:
            continue

    try:
        storage_relative = to_storage_relative(resolved_target)
        if not Path(storage_relative).is_absolute():
            return storage_relative
    except ValueError:
        pass

    try:
        return to_protected_media_relative(resolved_target)
    except ValueError as protected_exc:
        raise ValueError(
            f"Could not derive streamable relative path for {resolved_target}"
        ) from protected_exc


def _is_sha256_hex(value: str) -> bool:
    value = value.strip()
    return len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


def _source_size(video_field_file: FieldFile | Any, source_path: Path | None) -> int:
    if source_path is not None and source_path.is_file():
        return source_path.stat().st_size
    return field_file_size(video_field_file)


def _source_bytes(
    video_field_file: FieldFile | Any,
    source_path: Path | None,
    *,
    start: int,
    end: int,
) -> Any:
    if source_path is not None:
        return iter_file_path_bytes(source_path, start=start, end=end)
    return iter_field_file_bytes(video_field_file, start=start, end=end)


def _source_hash(video_field_file: FieldFile | Any, source_path: Path | None) -> str:
    if source_path is not None:
        return sha256_file(source_path)
    file_size = _source_size(video_field_file, source_path)
    digest = hashlib.sha256()
    if file_size <= 0:
        return digest.hexdigest()
    for chunk in _source_bytes(
        video_field_file,
        source_path,
        start=0,
        end=file_size - 1,
    ):
        digest.update(chunk)
    return digest.hexdigest()


def _local_plaintext_source_path(field_file_name: str) -> Path | None:
    relative_name = Path(field_file_name)
    if not relative_name.is_absolute():
        storage_roots = (
            Path(path_utils.STORAGE_DIR).resolve(),
            path_utils.EndoregPathsModel.from_environment().storage.resolve(),
        )
        for storage_root in dict.fromkeys(storage_roots):
            storage_candidate = storage_root / relative_name
            if storage_candidate.exists() and not _is_encrypted_file(storage_candidate):
                return storage_candidate
    return local_plaintext_path_from_name(field_file_name)


def _streamable_target_matches_source(
    *,
    target_path: Path,
    video_field_file: FieldFile | Any,
    source_path: Path | None,
    expected_hash: str,
    verify_source_hash: bool = True,
) -> bool:
    if not target_path.exists() or _is_encrypted_file(target_path):
        return False

    try:
        if target_path.stat().st_size <= 0:
            return False
    except OSError:
        return False
    if not _is_faststart_mp4(target_path):
        return False

    if verify_source_hash and expected_hash and _is_sha256_hex(expected_hash):
        try:
            return _source_hash(video_field_file, source_path) == expected_hash
        except OSError:
            return False
    return True


def _materialize_streamable_target(
    video_field_file: FieldFile | Any,
    target_path: Path,
    *,
    source_path: Path | None = None,
    expected_hash: str = "",
    transcode_profile: StreamableTranscodeProfile = DEFAULT_STREAMABLE_TRANSCODE_PROFILE,
) -> Path:
    file_size = _source_size(video_field_file, source_path)
    temp_source = target_path.with_name(
        f".{target_path.name}.source.{os.getpid()}.{uuid4().hex}.tmp"
    )

    try:
        if file_size <= 0:
            raise RuntimeError(f"Refusing empty streamable source: {target_path}")
        if (
            expected_hash
            and _is_sha256_hex(expected_hash)
            and _source_hash(video_field_file, source_path) != expected_hash
        ):
            raise RuntimeError(
                f"Refusing streamable source with unexpected hash: {target_path}"
            )

        ffmpeg_source_path = source_path
        if ffmpeg_source_path is None:
            ffmpeg_source_path = atomic_write_file(
                destination=temp_source,
                content=_source_bytes(
                    video_field_file,
                    source_path,
                    start=0,
                    end=file_size - 1,
                ),
                required_bytes=file_size,
                file_mode=STREAMABLE_FILE_MODE,
                dir_mode=STREAMABLE_DIRECTORY_MODE,
            )
            if _is_encrypted_file(ffmpeg_source_path):
                raise RuntimeError(
                    f"Refusing encrypted streamable source: {target_path}"
                )

        return _transcode_streamable_mp4(
            ffmpeg_source_path,
            target_path,
            profile=transcode_profile,
        )
    finally:
        safe_unlink_file(temp_source, missing_ok=True)


def _configured_streamable_raw_video_root() -> Path:
    if _streamable_raw_video_root is not _DEFAULT_STREAMABLE_RAW_VIDEO_ROOT_FN:
        return Path(_streamable_raw_video_root()).resolve()
    if STREAMABLE_RAW_VIDEO_ROOT != _DEFAULT_STREAMABLE_RAW_VIDEO_ROOT:
        return Path(STREAMABLE_RAW_VIDEO_ROOT).resolve()
    return Path(_streamable_raw_video_root()).resolve()


def _configured_streamable_processed_video_root() -> Path:
    if (
        _streamable_processed_video_root
        is not _DEFAULT_STREAMABLE_PROCESSED_VIDEO_ROOT_FN
    ):
        return Path(_streamable_processed_video_root()).resolve()
    if STREAMABLE_PROCESSED_VIDEO_ROOT != _DEFAULT_STREAMABLE_PROCESSED_VIDEO_ROOT:
        return Path(STREAMABLE_PROCESSED_VIDEO_ROOT).resolve()
    return Path(_streamable_processed_video_root()).resolve()


def _streamable_root_for_kind(kind: StreamableArtifactKind) -> Path:
    match kind:
        case StreamableArtifactKind.RAW:
            return _configured_streamable_raw_video_root()
        case StreamableArtifactKind.PROCESSED:
            return _configured_streamable_processed_video_root()


def _video_streamable_target(
    video: "VideoFile",
    *,
    spec: StreamableArtifactSpec,
) -> Path:
    stem = getattr(video, spec.hash_attr, None) or video.video_hash
    root = _streamable_root_for_kind(spec.kind)
    return root / f"{stem}{MP4_SUFFIX}"


def resolve_streamable_media_state(
    video: "VideoFile",
    *,
    include_raw: bool = True,
    include_processed: bool = True,
) -> StreamableMediaState:
    return build_streamable_media_state(
        video,
        include_raw=include_raw,
        include_processed=include_processed,
        target_path_for_spec=_video_streamable_target,
        resolve_policy=resolve_storage_policy,
    )


def _sync_one_streamable(
    *,
    video_field_file: FieldFile | Any,
    target_path: Path,
    current_relative_path: str,
    expected_hash: str,
    save: bool,
) -> tuple[str, bool]:
    """
    Returns:
        (relative_path, materialized_or_verified)
    """
    relative_path = _streamable_relative_path(target_path)
    field_file_name = getattr(video_field_file, "name", None)
    source_path = (
        _local_plaintext_source_path(field_file_name)
        if isinstance(field_file_name, str)
        else None
    )

    if current_relative_path == relative_path and _streamable_target_matches_source(
        target_path=target_path,
        video_field_file=video_field_file,
        source_path=source_path,
        expected_hash=expected_hash,
        verify_source_hash=False,
    ):
        return relative_path, True

    if _streamable_target_matches_source(
        target_path=target_path,
        video_field_file=video_field_file,
        source_path=source_path,
        expected_hash=expected_hash,
    ):
        return relative_path, True

    if not save:
        return relative_path, False

    previous_mtime_ns = None
    if target_path.exists():
        previous_mtime_ns = target_path.stat().st_mtime_ns

    materialized_path = _materialize_streamable_target(
        video_field_file,
        target_path,
        source_path=source_path,
        expected_hash=expected_hash,
    )
    if previous_mtime_ns is not None:
        ensure_file_mtime_after(
            materialized_path,
            previous_mtime_ns=previous_mtime_ns,
        )
    return _streamable_relative_path(materialized_path), True


def sync_video_streamable_artifacts(
    video: "VideoFile",
    *,
    include_raw: bool = True,
    include_processed: bool = True,
    save: bool = True,
) -> list[str]:
    update_fields: list[str] = []
    state = resolve_streamable_media_state(
        video,
        include_raw=include_raw,
        include_processed=include_processed,
    )
    synced_by_kind = {spec.kind: False for spec in STREAMABLE_ARTIFACT_SPECS}

    for decision in state.artifacts:
        if decision.disposition == StreamableArtifactDisposition.SYNC:
            if decision.field_file is None or decision.target_path is None:
                raise RuntimeError(
                    "Invalid streamable sync decision without source or target"
                )
            relative_path, synced = _sync_one_streamable(
                video_field_file=decision.field_file,
                target_path=decision.target_path,
                current_relative_path=decision.current_relative_path,
                expected_hash=decision.expected_hash,
                save=save,
            )
            synced_by_kind[decision.spec.kind] = synced
            if synced and decision.current_relative_path != relative_path:
                setattr(video, decision.spec.relative_path_attr, relative_path)
                update_fields.append(decision.spec.relative_path_attr)
            continue

        if decision.disposition == StreamableArtifactDisposition.CLEAR_STALE_PATH:
            logger.info(
                "Skipping %s streamable artifact sync for video %s because "
                "ENDOREG_STORAGE_PROFILE routes %s video to %s.",
                decision.spec.kind.value,
                getattr(video, "pk", "unknown"),
                decision.spec.kind.value,
                decision.storage_policy,
            )
            setattr(video, decision.spec.relative_path_attr, "")
            update_fields.append(decision.spec.relative_path_attr)

    has_verified_streamable = any(synced_by_kind.values())

    if not has_verified_streamable:
        for spec in STREAMABLE_ARTIFACT_SPECS:
            if getattr(video, spec.relative_path_attr, ""):
                setattr(video, spec.relative_path_attr, "")
                update_fields.append(spec.relative_path_attr)

    storage_mode_cls = type(video).StorageMode
    preferred_storage_mode = (
        storage_mode_cls.STREAMABLE
        if has_verified_streamable
        else storage_mode_cls.ENCRYPTED
    )

    if video.storage_mode != preferred_storage_mode:
        video.storage_mode = preferred_storage_mode
        update_fields.append("storage_mode")
    if save and update_fields:
        video.save(update_fields=list(dict.fromkeys(update_fields)))
        logger.info(
            "Synchronized streamable video artifacts for %s (%s)",
            video.pk,
            ", ".join(dict.fromkeys(update_fields)),
        )
    return list(dict.fromkeys(update_fields))

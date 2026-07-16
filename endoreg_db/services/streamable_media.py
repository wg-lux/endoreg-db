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
    atomic_move_file,
    atomic_write_file,
    ensure_directory,
    secure_unlink_file,
    sha256_file,
)
from endoreg_db.utils.paths import (
    protected_media_root,
    to_protected_media_relative,
    to_storage_relative,
)
from endoreg_db.utils.storage_profile import resolve_storage_policy
from endoreg_db.utils.storage import save_local_file
from endoreg_db.utils.storage_streaming import (
    field_file_size,
    iter_file_path_bytes,
    iter_field_file_bytes,
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


def _streamable_relative_path(  # pyright: ignore[reportUnusedFunction]
    target_path: Path,
) -> str:
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


def _repair_plaintext_field_file(field_file: FieldFile | Any | None) -> bool:
    if field_file is None:
        return False
    field_file_name = getattr(field_file, "name", None)
    if not isinstance(field_file_name, str) or not field_file_name:
        return False
    storage = getattr(field_file, "storage", None)
    repair_plaintext_file = getattr(storage, "repair_plaintext_file", None)
    if not callable(repair_plaintext_file):
        return False
    return bool(repair_plaintext_file(field_file_name))


def _field_file_has_name(field_file: object) -> bool:
    return isinstance(getattr(field_file, "name", None), str) and bool(
        getattr(field_file, "name", "")
    )


def _materialize_processed_hls(video: "VideoFile", *, force: bool) -> None:
    processed_file = getattr(video, "processed_file", None)
    if not _field_file_has_name(processed_file):
        return

    video_pk = getattr(video, "pk", None)
    if video_pk is None:
        raise RuntimeError("Cannot materialize processed HLS for an unsaved video.")

    from endoreg_db.services.hls_media import materialize_video_hls

    result = materialize_video_hls(
        int(video_pk),
        artifact_kind="processed",
        force=force,
    )
    logger.info(
        "Materialized processed HLS from streamable sync: video=%s status=%s key_id=%s",
        video_pk,
        result.status,
        result.key_id,
    )


def _streamable_path_from_relative(relative_path: str) -> Path | None:
    normalized = str(relative_path or "").strip()
    if not normalized:
        return None
    resolved = path_utils.resolve_existing_protected_media_path(normalized)
    if resolved is not None:
        return resolved

    relative = Path(normalized)
    candidate_roots = (
        Path(path_utils.STORAGE_DIR).resolve(),
        _streamable_root_for_kind(StreamableArtifactKind.RAW).parent.parent,
        _streamable_root_for_kind(StreamableArtifactKind.PROCESSED).parent.parent,
    )
    for root in dict.fromkeys(path.resolve() for path in candidate_roots):
        candidate = root / relative
        if candidate.exists():
            return candidate.resolve()
    return None


def _canonical_storage_name_for_legacy_streamable(
    video: "VideoFile",
    decision: StreamableArtifactDecision,
    source_path: Path,
) -> str:
    expected_hash = (
        decision.expected_hash if _is_sha256_hex(decision.expected_hash) else ""
    )
    content_hash = expected_hash or sha256_file(source_path)
    if decision.spec.kind == StreamableArtifactKind.RAW:
        target_path = path_utils.SENSITIVE_VIDEO_DIR / f"{content_hash}{MP4_SUFFIX}"
    else:
        raw_hash = str(getattr(video, "video_hash", "") or "").strip()
        stem = f"{raw_hash}.{content_hash}" if raw_hash else content_hash
        target_path = path_utils.ANONYM_VIDEO_DIR / f"{stem}{MP4_SUFFIX}"
    return to_storage_relative(target_path)


def _storage_path_for_name(field_file: FieldFile | Any, name: str) -> Path | None:
    storage = getattr(field_file, "storage", None)
    storage_path = getattr(storage, "path", None)
    if not callable(storage_path):
        return None
    try:
        raw_path = storage_path(name)
    except (NotImplementedError, OSError, ValueError):
        return None
    if isinstance(raw_path, Path):
        return raw_path
    if isinstance(raw_path, str):
        return Path(raw_path)
    return None


def _rehome_referenced_streamable_field_file(
    *,
    video: "VideoFile",
    decision: StreamableArtifactDecision,
    streamable_path: Path,
) -> bool:
    if decision.field_file is None:
        return False
    if decision.field_file_name != decision.current_relative_path:
        return False

    target_name = _canonical_storage_name_for_legacy_streamable(
        video,
        decision,
        streamable_path,
    )
    if target_name == decision.field_file_name:
        return False

    if _is_encrypted_file(streamable_path):
        destination_path = _storage_path_for_name(decision.field_file, target_name)
        if destination_path is None:
            return False
        ensure_directory(destination_path.parent)
        atomic_move_file(source=streamable_path, destination=destination_path)
        decision.field_file.name = target_name
    else:
        save_local_file(
            decision.field_file,
            streamable_path,
            name=target_name,
            save=False,
            overwrite=True,
        )
        secure_unlink_file(streamable_path, missing_ok=True)

    logger.warning(
        "Moved canonical %s FieldFile for video %s out of legacy streamable path "
        "%s into encrypted app storage name %s.",
        decision.spec.kind.value,
        getattr(video, "pk", "unknown"),
        decision.current_relative_path,
        target_name,
    )
    return True


def _secure_delete_legacy_streamable_path(
    *,
    relative_path: str,
    field_file_name: str,
) -> bool:
    streamable_path = _streamable_path_from_relative(relative_path)
    if streamable_path is None or not streamable_path.exists():
        return False
    if field_file_name and field_file_name == relative_path:
        logger.warning(
            "Not deleting streamable path %s because it is still referenced by a "
            "canonical FieldFile; encryption repair was attempted instead.",
            relative_path,
        )
        return False
    secure_unlink_file(streamable_path, missing_ok=True)
    return True


def _materialize_streamable_target(  # pyright: ignore[reportUnusedFunction]
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
        secure_unlink_file(temp_source, missing_ok=True)


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
    stem_value: object = getattr(video, spec.hash_attr, None) or getattr(
        video,
        "video_hash",
        "",
    )
    stem = str(stem_value)
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


def sync_video_streamable_artifacts(
    video: "VideoFile",
    *,
    include_raw: bool = True,
    include_processed: bool = True,
    save: bool = True,
    force: bool = False,
) -> list[str]:
    update_fields: list[str] = []
    state = resolve_streamable_media_state(
        video,
        include_raw=include_raw,
        include_processed=include_processed,
    )

    for decision in state.artifacts:
        repaired = _repair_plaintext_field_file(decision.field_file) if save else False
        if repaired:
            logger.info(
                "Re-encrypted plaintext canonical %s video payload for video %s.",
                decision.spec.kind.value,
                getattr(video, "pk", "unknown"),
            )

        if not decision.current_relative_path:
            continue

        if save:
            streamable_path = _streamable_path_from_relative(
                decision.current_relative_path
            )
            rehomed = (
                _rehome_referenced_streamable_field_file(
                    video=video,
                    decision=decision,
                    streamable_path=streamable_path,
                )
                if streamable_path is not None and streamable_path.exists()
                else False
            )
            deleted = (
                False
                if rehomed
                else _secure_delete_legacy_streamable_path(
                    relative_path=decision.current_relative_path,
                    field_file_name=decision.field_file_name,
                )
            )
            logger.info(
                "Clearing legacy %s streamable path for video %s "
                "(deleted=%s rehomed=%s storage_policy=%s disposition=%s).",
                decision.spec.kind.value,
                getattr(video, "pk", "unknown"),
                deleted,
                rehomed,
                decision.storage_policy,
                decision.disposition,
            )
            setattr(video, decision.spec.relative_path_attr, "")
            if rehomed:
                update_fields.append(decision.spec.file_attr)
        update_fields.append(decision.spec.relative_path_attr)

    storage_mode_cls = type(video).StorageMode
    preferred_storage_mode = storage_mode_cls.ENCRYPTED
    current_storage_mode: object = getattr(video, "storage_mode", None)

    if current_storage_mode != preferred_storage_mode:
        if save:
            setattr(video, "storage_mode", preferred_storage_mode)
        update_fields.append("storage_mode")
    unique_update_fields = list(dict.fromkeys(update_fields))
    if save and unique_update_fields:
        video.save(update_fields=unique_update_fields)
        logger.info(
            "Removed legacy streamable video artifacts for %s (%s)",
            video.pk,
            ", ".join(unique_update_fields),
        )
    if save and force and include_processed:
        _materialize_processed_hls(video, force=True)
    return unique_update_fields

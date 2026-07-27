from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from collections import deque
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Literal, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.db import transaction
from django.db.models.fields.files import FieldFile
from django.utils import timezone

from endoreg_db.config.env import (
    get_ffmpeg_transcode_quality_mode,
    get_ffmpeg_transcode_timeout_seconds,
)
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.schemas.video_storage import VideoArtifactProbe
from endoreg_db.services import streamable_media
from endoreg_db.services.video_files import (
    VideoArtifactKind,
    get_active_raw_video_file,
)
from endoreg_db.utils import ffmpeg_wrapper
from endoreg_db.utils.video.command_construction import FFprobeInputPolicy
from endoreg_db.utils.video.encoding_standard import STANDARD_VIDEO_ENCODING
from endoreg_db.services.video_storage_normalization import (
    configured_video_storage_profile,
    normalize_video_file,
    probe_video_artifact,
    validate_normalized_output,
)
from endoreg_db.utils.encryption.encryption import load_master_key
from endoreg_db.utils.filesystem.file_operations import atomic_write_file
from endoreg_db.utils.file_operations import (
    atomic_move_path,
    ensure_disk_capacity,
    ensure_directory,
    safe_rmtree,
    safe_unlink_file,
    secure_unlink_file,
    set_path_mode,
)
from endoreg_db.utils.media_urls import (
    build_video_hls_key_path,
    build_video_hls_segment_base_path,
)
from endoreg_db.utils.paths import (
    EndoregPathsModel,
    ensure_within_protected_media_root,
    ensure_within_protected_root,
    resolve_existing_protected_media_path,
    to_protected_media_relative,
)

logger = logging.getLogger(__name__)

HlsArtifactKind = Literal["raw", "processed"]

HLS_CONTENT_KEY_BYTES = 16
HLS_IV_HEX_LENGTH = 32
HLS_DIRECTORY_MODE = 0o750
HLS_FILE_MODE = 0o640
HLS_TEMP_DIRECTORY_MODE = 0o700
HLS_TEMP_FILE_MODE = 0o600
HLS_KEY_WRAP_ALGORITHM = "AESGCM-master-wrap-v1"
HLS_KEY_WRAP_NONCE_BYTES = 12
HLS_MATERIALIZATION_STALE_GRACE_SECONDS = 60
FFMPEG_STDIN_CHUNK_BYTES = 1024 * 1024
FFMPEG_STDERR_TAIL_BYTES = 64 * 1024
FFMPEG_PROCESS_POLL_INTERVAL_SECONDS = 1.0
FFMPEG_OUTPUT_PROGRESS_WATCHDOG_SECONDS = 300.0
MP4_PIPE_COMPATIBILITY_SCAN_BYTES = 64 * 1024
HLS_VIDEO_PRESET = "medium"
HLS_VIDEO_CRF = "18"
HLS_VIDEO_PROFILE = STANDARD_VIDEO_ENCODING.profile
HLS_VIDEO_PIXEL_FORMAT = STANDARD_VIDEO_ENCODING.pixel_format
HLS_VIDEO_COLOR_RANGE = STANDARD_VIDEO_ENCODING.color_range
HLS_AUDIO_CODEC = "copy"
HLS_FFMPEG_THREADS_ENV = "LX_ANNOTATE_HLS_FFMPEG_THREADS"
HLS_KEY_URI_PATTERN = re.compile(r'URI="[^"]+"')

_HLS_KEY_WRAP_AAD_PREFIX = b"endoreg-db:hls-content-key:v1"


@dataclass(frozen=True)
class HlsMaterializationResult:
    video_id: int
    artifact_kind: HlsArtifactKind
    status: str
    key_id: str
    playlist_relative_path: str
    segment_directory_relative_path: str
    segment_count: int
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "video_id": self.video_id,
            "artifact_kind": self.artifact_kind,
            "status": self.status,
            "key_id": self.key_id,
            "playlist_relative_path": self.playlist_relative_path,
            "segment_directory_relative_path": self.segment_directory_relative_path,
            "segment_count": self.segment_count,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class _ArtifactSnapshot:
    status: str
    key_id: UUID
    source_generation_id: UUID
    key_ciphertext: bytes | None
    key_nonce: bytes | None
    key_wrap_algorithm: str
    iv_hex: str
    playlist_relative_path: str
    segment_directory_relative_path: str
    segment_count: int
    source_file_name: str


@dataclass(frozen=True)
class _PreparedArtifact:
    artifact_id: int
    key_id: UUID
    previous: _ArtifactSnapshot | None
    should_materialize: bool


@dataclass(frozen=True)
class HlsMaterializationDispatchReservation:
    artifact_id: int
    artifact_kind: HlsArtifactKind
    status: Literal["queued", "already_queued", "already_ready"]


@dataclass(frozen=True)
class _HlsSource:
    source_file_name: str
    field_file: FieldFile


@dataclass(frozen=True)
class _HlsOutputProgress:
    file_count: int
    total_bytes: int
    latest_mtime_ns: int


def _coerce_hls_artifact_kind(value: object) -> VideoArtifactKind:
    if isinstance(value, VideoArtifactKind):
        return value
    normalized = str(value).strip().lower()
    if normalized == VideoArtifactKind.RAW.value:
        return VideoArtifactKind.RAW
    if normalized == VideoArtifactKind.PROCESSED.value:
        return VideoArtifactKind.PROCESSED
    raise ValueError(f"Unsupported HLS artifact kind: {value!r}")


def coerce_hls_artifact_kind(value: object) -> VideoArtifactKind:
    """Parse the local, authenticated HLS artifact kind."""
    return _coerce_hls_artifact_kind(value)


def _field_file_has_name(field_file: object) -> bool:
    return isinstance(getattr(field_file, "name", None), str) and bool(
        getattr(field_file, "name", "")
    )


def _source_field_file(
    video: VideoFile,
    artifact_kind: VideoArtifactKind,
) -> FieldFile:
    if artifact_kind == VideoArtifactKind.PROCESSED:
        field_file = getattr(video, "processed_file", None)
        if not _field_file_has_name(field_file):
            raise FileNotFoundError("Video has no processed source file for HLS")
        return cast(FieldFile, field_file)

    try:
        field_file = get_active_raw_video_file(video)
    except ValueError as exc:
        raise FileNotFoundError("Video has no active raw source file for HLS") from exc
    if not _field_file_has_name(field_file):
        raise FileNotFoundError("Video has no active raw source file for HLS")
    return field_file


def _hls_source(video: VideoFile, artifact_kind: VideoArtifactKind) -> _HlsSource:
    field_file = _source_field_file(video, artifact_kind)
    return _HlsSource(
        source_file_name=str(field_file.name),
        field_file=field_file,
    )


def _key_wrap_aad(
    *,
    video_id: int,
    artifact_kind: VideoArtifactKind,
    key_id: UUID,
) -> bytes:
    return (
        _HLS_KEY_WRAP_AAD_PREFIX
        + f":video={video_id}:kind={artifact_kind.value}:key={key_id}".encode("ascii")
    )


def _wrap_hls_content_key(
    *,
    cek: bytes,
    video_id: int,
    artifact_kind: VideoArtifactKind,
    key_id: UUID,
) -> tuple[bytes, bytes]:
    if len(cek) != HLS_CONTENT_KEY_BYTES:
        raise ValueError("HLS content encryption key must be 16 bytes")
    nonce = os.urandom(HLS_KEY_WRAP_NONCE_BYTES)
    ciphertext = AESGCM(load_master_key()).encrypt(
        nonce,
        cek,
        _key_wrap_aad(
            video_id=video_id,
            artifact_kind=artifact_kind,
            key_id=key_id,
        ),
    )
    return ciphertext, nonce


def unwrap_hls_content_key(artifact: VideoHlsArtifact) -> bytes:
    if artifact.status != VideoHlsArtifact.Status.READY.value:
        raise ValueError("HLS artifact is not ready")
    if artifact.key_wrap_algorithm != HLS_KEY_WRAP_ALGORITHM:
        raise ValueError(
            f"Unsupported HLS key wrap algorithm: {artifact.key_wrap_algorithm}"
        )
    if artifact.key_ciphertext is None or artifact.key_nonce is None:
        raise ValueError("HLS artifact has no stored content key")

    artifact_kind = _coerce_hls_artifact_kind(artifact.artifact_kind)
    plaintext = AESGCM(load_master_key()).decrypt(
        bytes(artifact.key_nonce),
        bytes(artifact.key_ciphertext),
        _key_wrap_aad(
            video_id=int(artifact.video_id),
            artifact_kind=artifact_kind,
            key_id=artifact.key_id,
        ),
    )
    if len(plaintext) != HLS_CONTENT_KEY_BYTES:
        raise ValueError("Stored HLS content key has invalid length")
    return plaintext


def _artifact_snapshot(
    artifact: VideoHlsArtifact,
    *,
    allow_queued_ready_artifact: bool = False,
) -> _ArtifactSnapshot | None:
    is_ready = artifact.status == VideoHlsArtifact.Status.READY.value
    is_queued_ready = (
        allow_queued_ready_artifact
        and artifact.status == VideoHlsArtifact.Status.QUEUED.value
        and artifact.key_ciphertext is not None
        and artifact.key_nonce is not None
        and bool(artifact.iv_hex)
        and _ready_artifact_paths_exist(artifact)
    )
    if not is_ready and not is_queued_ready:
        return None
    return _ArtifactSnapshot(
        status=VideoHlsArtifact.Status.READY.value,
        key_id=artifact.key_id,
        source_generation_id=artifact.source_generation_id,
        key_ciphertext=(
            bytes(artifact.key_ciphertext)
            if artifact.key_ciphertext is not None
            else None
        ),
        key_nonce=bytes(artifact.key_nonce) if artifact.key_nonce is not None else None,
        key_wrap_algorithm=str(artifact.key_wrap_algorithm),
        iv_hex=str(artifact.iv_hex),
        playlist_relative_path=str(artifact.playlist_relative_path),
        segment_directory_relative_path=str(artifact.segment_directory_relative_path),
        segment_count=int(artifact.segment_count),
        source_file_name=str(artifact.source_file_name),
    )


def _restore_artifact_snapshot(
    artifact: VideoHlsArtifact,
    snapshot: _ArtifactSnapshot,
    *,
    last_error: str,
) -> None:
    artifact.status = snapshot.status
    artifact.key_id = snapshot.key_id
    artifact.source_generation_id = snapshot.source_generation_id
    artifact.key_ciphertext = snapshot.key_ciphertext
    artifact.key_nonce = snapshot.key_nonce
    artifact.key_wrap_algorithm = snapshot.key_wrap_algorithm
    artifact.iv_hex = snapshot.iv_hex
    artifact.playlist_relative_path = snapshot.playlist_relative_path
    artifact.segment_directory_relative_path = snapshot.segment_directory_relative_path
    artifact.segment_count = snapshot.segment_count
    artifact.source_file_name = snapshot.source_file_name
    artifact.last_error = last_error
    artifact.error_code = VideoHlsArtifact.ErrorCode.NONE.value
    artifact.save(
        update_fields=[
            "status",
            "key_id",
            "source_generation_id",
            "key_ciphertext",
            "key_nonce",
            "key_wrap_algorithm",
            "iv_hex",
            "playlist_relative_path",
            "segment_directory_relative_path",
            "segment_count",
            "source_file_name",
            "last_error",
            "error_code",
            "updated_at",
        ]
    )


def _mark_artifact_failed(
    *,
    artifact_id: int,
    error: str,
    previous: _ArtifactSnapshot | None,
    expected_key_id: UUID | None = None,
    expected_status: str | None = None,
    error_code: str = VideoHlsArtifact.ErrorCode.MATERIALIZATION_FAILED,
) -> bool:
    with transaction.atomic():
        artifact = VideoHlsArtifact.objects.select_for_update().get(pk=artifact_id)
        if expected_key_id is not None and artifact.key_id != expected_key_id:
            logger.warning(
                "Ignored stale HLS failure update: artifact=%s expected_key=%s current_key=%s",
                artifact_id,
                expected_key_id,
                artifact.key_id,
            )
            return False
        if expected_status is not None and artifact.status != expected_status:
            logger.warning(
                "Ignored HLS failure update from non-owner state: artifact=%s expected_status=%s current_status=%s",
                artifact_id,
                expected_status,
                artifact.status,
            )
            return False
        if previous is not None:
            _restore_artifact_snapshot(artifact, previous, last_error=error[:4000])
            return True

        artifact.status = VideoHlsArtifact.Status.FAILED.value
        artifact.key_ciphertext = None
        artifact.key_nonce = None
        artifact.iv_hex = ""
        artifact.playlist_relative_path = ""
        artifact.segment_directory_relative_path = ""
        artifact.segment_count = 0
        artifact.last_error = error[:4000]
        artifact.error_code = error_code
        artifact.save(
            update_fields=[
                "status",
                "key_ciphertext",
                "key_nonce",
                "iv_hex",
                "playlist_relative_path",
                "segment_directory_relative_path",
                "segment_count",
                "last_error",
                "error_code",
                "updated_at",
            ]
        )
        return True


def _materialization_stale_before() -> datetime:
    return timezone.now() - timedelta(
        seconds=get_ffmpeg_transcode_timeout_seconds()
        + HLS_MATERIALIZATION_STALE_GRACE_SECONDS
    )


def _recover_stale_in_flight_artifact(
    artifact: VideoHlsArtifact,
    *,
    video_id: int,
    artifact_kind: VideoArtifactKind,
    include_queued: bool,
) -> bool:
    recoverable_statuses = {VideoHlsArtifact.Status.MATERIALIZING.value}
    if include_queued:
        recoverable_statuses.add(VideoHlsArtifact.Status.QUEUED.value)
    if artifact.status not in recoverable_statuses:
        return False
    if artifact.updated_at > _materialization_stale_before():
        return False

    stale_status = str(artifact.status)
    recovery_error = (
        f"Recovered stale HLS {stale_status} attempt after exceeding the "
        "configured FFmpeg timeout."
    )
    if stale_status == VideoHlsArtifact.Status.QUEUED.value:
        queued_ready_snapshot = _artifact_snapshot(
            artifact,
            allow_queued_ready_artifact=True,
        )
        if queued_ready_snapshot is not None:
            _restore_artifact_snapshot(
                artifact,
                queued_ready_snapshot,
                last_error=recovery_error,
            )
            logger.warning(
                "Recovered stale queued HLS attempt to previous READY artifact: video=%s kind=%s artifact=%s",
                video_id,
                artifact_kind.value,
                artifact.pk,
            )
            return True

    artifact.status = VideoHlsArtifact.Status.FAILED.value
    artifact.key_ciphertext = None
    artifact.key_nonce = None
    artifact.iv_hex = ""
    artifact.playlist_relative_path = ""
    artifact.segment_directory_relative_path = ""
    artifact.segment_count = 0
    artifact.last_error = recovery_error
    artifact.error_code = VideoHlsArtifact.ErrorCode.STALE_ATTEMPT.value
    artifact.save(
        update_fields=[
            "status",
            "key_ciphertext",
            "key_nonce",
            "iv_hex",
            "playlist_relative_path",
            "segment_directory_relative_path",
            "segment_count",
            "last_error",
            "error_code",
            "updated_at",
        ]
    )
    logger.warning(
        "Recovered stale HLS attempt: video=%s kind=%s artifact=%s previous_status=%s",
        video_id,
        artifact_kind.value,
        artifact.pk,
        stale_status,
    )
    return True


def reserve_hls_materialization_dispatch(
    *,
    video_id: int,
    artifact_kind: object = VideoArtifactKind.PROCESSED,
    force: bool = False,
) -> HlsMaterializationDispatchReservation:
    """Atomically reserve one HLS task per video and artifact kind."""
    parsed_kind = coerce_hls_artifact_kind(artifact_kind)
    with transaction.atomic():
        video = VideoFile.objects.select_for_update().get(pk=int(video_id))
        artifact, created = VideoHlsArtifact.objects.select_for_update().get_or_create(
            video=video,
            artifact_kind=parsed_kind.value,
            defaults={"status": VideoHlsArtifact.Status.QUEUED.value},
        )
        recovered_stale_artifact = _recover_stale_in_flight_artifact(
            artifact,
            video_id=int(video.pk),
            artifact_kind=parsed_kind,
            include_queued=True,
        )
        if not created and artifact.status in {
            VideoHlsArtifact.Status.QUEUED.value,
            VideoHlsArtifact.Status.MATERIALIZING.value,
        }:
            return HlsMaterializationDispatchReservation(
                artifact_id=int(artifact.pk),
                artifact_kind=parsed_kind.value,
                status="already_queued",
            )
        if (
            artifact.status == VideoHlsArtifact.Status.READY.value
            and not force
            and _ready_artifact_paths_exist(artifact)
            and artifact.source_file_name
            == _hls_source(video, parsed_kind).source_file_name
        ):
            return HlsMaterializationDispatchReservation(
                artifact_id=int(artifact.pk),
                artifact_kind=parsed_kind.value,
                status="already_ready",
            )

        artifact.status = VideoHlsArtifact.Status.QUEUED.value
        if not recovered_stale_artifact:
            artifact.last_error = ""
        artifact.error_code = VideoHlsArtifact.ErrorCode.NONE.value
        artifact.save(
            update_fields=["status", "last_error", "error_code", "updated_at"]
        )
        return HlsMaterializationDispatchReservation(
            artifact_id=int(artifact.pk),
            artifact_kind=parsed_kind.value,
            status="queued",
        )


def mark_hls_materialization_dispatch_failed(*, artifact_id: int, error: str) -> None:
    """Release a reservation when publishing its Celery task failed."""
    with transaction.atomic():
        artifact = VideoHlsArtifact.objects.select_for_update().get(pk=artifact_id)
        if artifact.status != VideoHlsArtifact.Status.QUEUED.value:
            return
        _mark_artifact_failed(
            artifact_id=artifact_id,
            error=error,
            previous=None,
            expected_status=VideoHlsArtifact.Status.QUEUED.value,
            error_code=VideoHlsArtifact.ErrorCode.DISPATCH_FAILED.value,
        )


def _prepare_artifact_record(
    *,
    video_id: int,
    artifact_kind: VideoArtifactKind,
    source_file_name: str,
    key_id: UUID,
    key_ciphertext: bytes,
    key_nonce: bytes,
    iv_hex: str,
    force: bool,
) -> _PreparedArtifact:
    with transaction.atomic():
        video = VideoFile.objects.select_for_update().get(pk=video_id)
        artifact, created = VideoHlsArtifact.objects.select_for_update().get_or_create(
            video=video,
            artifact_kind=artifact_kind.value,
            defaults={"status": VideoHlsArtifact.Status.MATERIALIZING.value},
        )
        if (
            artifact.status == VideoHlsArtifact.Status.READY.value
            and not force
            and _ready_artifact_paths_exist(artifact)
            and artifact.source_file_name == source_file_name
        ):
            return _PreparedArtifact(
                artifact_id=int(artifact.pk),
                key_id=artifact.key_id,
                previous=None,
                should_materialize=False,
            )
        if not created:
            _recover_stale_in_flight_artifact(
                artifact,
                video_id=video_id,
                artifact_kind=artifact_kind,
                include_queued=False,
            )
            if artifact.status == VideoHlsArtifact.Status.MATERIALIZING.value:
                return _PreparedArtifact(
                    artifact_id=int(artifact.pk),
                    key_id=artifact.key_id,
                    previous=None,
                    should_materialize=False,
                )

        previous = _artifact_snapshot(
            artifact,
            allow_queued_ready_artifact=True,
        )
        artifact.status = VideoHlsArtifact.Status.MATERIALIZING.value
        artifact.key_id = key_id
        artifact.source_generation_id = uuid4()
        artifact.key_ciphertext = key_ciphertext
        artifact.key_nonce = key_nonce
        artifact.key_wrap_algorithm = HLS_KEY_WRAP_ALGORITHM
        artifact.iv_hex = iv_hex
        artifact.playlist_relative_path = ""
        artifact.segment_directory_relative_path = ""
        artifact.segment_count = 0
        artifact.source_file_name = source_file_name
        artifact.last_error = ""
        artifact.error_code = VideoHlsArtifact.ErrorCode.NONE.value
        artifact.full_clean()
        artifact.save(
            update_fields=[
                "status",
                "key_id",
                "source_generation_id",
                "key_ciphertext",
                "key_nonce",
                "key_wrap_algorithm",
                "iv_hex",
                "playlist_relative_path",
                "segment_directory_relative_path",
                "segment_count",
                "source_file_name",
                "last_error",
                "error_code",
                "updated_at",
            ]
        )
        return _PreparedArtifact(
            artifact_id=int(artifact.pk),
            key_id=key_id,
            previous=previous,
            should_materialize=True,
        )


def _mark_artifact_ready(
    *,
    artifact_id: int,
    expected_key_id: UUID,
    playlist_relative_path: str,
    segment_directory_relative_path: str,
    segment_count: int,
) -> VideoHlsArtifact:
    with transaction.atomic():
        artifact = VideoHlsArtifact.objects.select_for_update().get(pk=artifact_id)
        if (
            artifact.status != VideoHlsArtifact.Status.MATERIALIZING.value
            or artifact.key_id != expected_key_id
        ):
            raise RuntimeError(
                "HLS materialization ownership was lost before READY publication: "
                f"artifact={artifact_id} expected_key={expected_key_id} "
                f"current_key={artifact.key_id} current_status={artifact.status}"
            )
        artifact.status = VideoHlsArtifact.Status.READY.value
        artifact.playlist_relative_path = playlist_relative_path
        artifact.segment_directory_relative_path = segment_directory_relative_path
        artifact.segment_count = segment_count
        artifact.last_error = ""
        artifact.error_code = VideoHlsArtifact.ErrorCode.NONE.value
        artifact.full_clean()
        artifact.save(
            update_fields=[
                "status",
                "playlist_relative_path",
                "segment_directory_relative_path",
                "segment_count",
                "last_error",
                "error_code",
                "updated_at",
            ]
        )
        return artifact


def _hls_root_for_kind(artifact_kind: VideoArtifactKind) -> Path:
    root = (
        streamable_media.STREAMABLE_RAW_VIDEO_ROOT
        if artifact_kind == VideoArtifactKind.RAW
        else streamable_media.STREAMABLE_PROCESSED_VIDEO_ROOT
    )
    return ensure_within_protected_media_root(Path(root).resolve() / "hls")


def _artifact_target_dir(
    *,
    video: VideoFile,
    artifact_kind: VideoArtifactKind,
    key_id: UUID,
) -> Path:
    return ensure_within_protected_media_root(
        _hls_root_for_kind(artifact_kind) / str(video.uuid) / str(key_id) / "v0"
    )


def _temporary_key_dir(*, video_id: int, key_id: UUID) -> Path:
    return ensure_within_protected_root(
        EndoregPathsModel.from_environment().transcoding
        / "hls_key_material"
        / str(video_id)
        / str(key_id)
    )


def _temporary_output_dir(*, video_id: int, key_id: UUID) -> Path:
    return ensure_within_protected_root(
        EndoregPathsModel.from_environment().transcoding
        / "hls_output"
        / str(video_id)
        / str(key_id)
    )


def _temporary_plaintext_source_dir(*, video_id: int, key_id: UUID) -> Path:
    return ensure_within_protected_root(
        EndoregPathsModel.from_environment().transcoding
        / "hls_plaintext_source"
        / str(video_id)
        / str(key_id)
    )


def _write_transient_key_files(
    *,
    temp_dir: Path,
    cek: bytes,
    key_uri: str,
    iv_hex: str,
) -> tuple[Path, Path]:
    ensure_directory(temp_dir, dir_mode=HLS_TEMP_DIRECTORY_MODE)
    key_path = temp_dir / "hls.key"
    key_info_path = temp_dir / "key_info.txt"
    atomic_write_file(
        destination=key_path,
        content=(cek,),
        required_bytes=len(cek),
        file_mode=HLS_TEMP_FILE_MODE,
        dir_mode=HLS_TEMP_DIRECTORY_MODE,
    )
    key_info_payload = f"{key_uri}\n{key_path}\n{iv_hex}\n".encode("utf-8")
    atomic_write_file(
        destination=key_info_path,
        content=(key_info_payload,),
        required_bytes=len(key_info_payload),
        file_mode=HLS_TEMP_FILE_MODE,
        dir_mode=HLS_TEMP_DIRECTORY_MODE,
    )
    return key_path, key_info_path


@contextmanager
def _temporary_hls_key_material(
    *,
    temp_key_dir: Path,
    cek: bytes,
    key_uri: str,
    iv_hex: str,
) -> Generator[tuple[Path, Path], None, None]:
    ensure_directory(temp_key_dir, dir_mode=HLS_TEMP_DIRECTORY_MODE)
    key_path = temp_key_dir / "hls.key"
    key_info_path = temp_key_dir / "key_info.txt"
    _write_transient_key_files(
        temp_dir=temp_key_dir,
        cek=cek,
        key_uri=key_uri,
        iv_hex=iv_hex,
    )
    try:
        yield key_path, key_info_path
    finally:
        try:
            safe_unlink_file(key_info_path, missing_ok=True)
        except Exception as cleanup_exc:
            logger.warning(
                "Failed to remove HLS key info file %s during cleanup: %s",
                key_info_path,
                cleanup_exc,
            )

        try:
            secure_unlink_file(key_path, missing_ok=True)
        except Exception as cleanup_exc:
            logger.warning(
                "Failed to remove HLS key file %s during cleanup: %s",
                key_path,
                cleanup_exc,
            )
        try:
            safe_rmtree(temp_key_dir, missing_ok=True)
        except Exception as cleanup_exc:
            logger.warning(
                "Failed to remove temporary HLS key dir %s during cleanup: %s",
                temp_key_dir,
                cleanup_exc,
            )


def _stderr_tail(chunks: deque[bytes]) -> str:
    return b"".join(chunks).decode("utf-8", errors="replace").strip()


def _drain_pipe_tail(pipe: BinaryIO, chunks: deque[bytes]) -> None:
    total_bytes = 0
    for chunk in iter(lambda: pipe.read(8192), b""):
        chunks.append(chunk)
        total_bytes += len(chunk)
        while total_bytes > FFMPEG_STDERR_TAIL_BYTES and chunks:
            total_bytes -= len(chunks.popleft())


def _source_size_bytes(field_file: FieldFile) -> int | None:
    try:
        value = int(getattr(field_file, "size"))
    except (AttributeError, TypeError, ValueError, OSError):
        return None
    return value if value > 0 else None


def _iter_prefixed_source_chunks(
    *,
    prefix: bytes,
    source: BinaryIO,
) -> Iterable[bytes]:
    if prefix:
        yield prefix
    while True:
        chunk = source.read(FFMPEG_STDIN_CHUNK_BYTES)
        if not chunk:
            break
        yield chunk


def _materialize_seekable_plaintext_source(
    *,
    source: BinaryIO,
    prefix: bytes,
    source_file_name: str,
    source_size_bytes: int | None,
    temp_source_dir: Path,
) -> Path:
    ensure_directory(temp_source_dir, dir_mode=HLS_TEMP_DIRECTORY_MODE)
    suffix = Path(source_file_name).suffix.lower() or ".mp4"
    destination = temp_source_dir / f"source{suffix}"
    if source_size_bytes is not None:
        ensure_disk_capacity(
            destination_dir=temp_source_dir,
            required_bytes=source_size_bytes,
        )
    atomic_write_file(
        destination=destination,
        content=_iter_prefixed_source_chunks(prefix=prefix, source=source),
        required_bytes=source_size_bytes,
        file_mode=HLS_TEMP_FILE_MODE,
        dir_mode=HLS_TEMP_DIRECTORY_MODE,
    )
    return destination


def _cleanup_seekable_plaintext_source(
    *,
    temp_source_dir: Path,
    source_path: Path | None,
) -> None:
    if source_path is not None:
        try:
            secure_unlink_file(source_path, missing_ok=True)
        except Exception as cleanup_exc:
            logger.warning(
                "Failed to securely remove temporary HLS source %s: %s",
                source_path,
                cleanup_exc,
            )
    try:
        safe_rmtree(temp_source_dir, missing_ok=True)
    except Exception as cleanup_exc:
        logger.warning(
            "Failed to remove temporary HLS source dir %s: %s",
            temp_source_dir,
            cleanup_exc,
        )


def _hls_output_progress(
    *,
    segment_dir: Path,
    playlist_path: Path,
) -> _HlsOutputProgress:
    file_count = 0
    total_bytes = 0
    latest_mtime_ns = 0
    candidates = tuple(segment_dir.glob("seg_*.ts")) + (playlist_path,)
    for path in candidates:
        try:
            stat_result = path.stat()
        except FileNotFoundError:
            continue
        if not path.is_file():
            continue
        file_count += 1
        total_bytes += int(stat_result.st_size)
        latest_mtime_ns = max(latest_mtime_ns, int(stat_result.st_mtime_ns))
    return _HlsOutputProgress(
        file_count=file_count,
        total_bytes=total_bytes,
        latest_mtime_ns=latest_mtime_ns,
    )


def _wait_for_ffmpeg_completion(
    *,
    process: subprocess.Popen[bytes],
    segment_pattern: Path,
    playlist_path: Path,
) -> int:
    started_at = time.monotonic()
    total_timeout_seconds = float(get_ffmpeg_transcode_timeout_seconds())
    last_progress_at = started_at
    last_progress = _hls_output_progress(
        segment_dir=segment_pattern.parent,
        playlist_path=playlist_path,
    )

    while True:
        try:
            return process.wait(timeout=FFMPEG_PROCESS_POLL_INTERVAL_SECONDS)
        except subprocess.TimeoutExpired:
            pass

        now = time.monotonic()
        if now - started_at > total_timeout_seconds:
            raise TimeoutError(
                "FFmpeg exceeded total HLS transcode timeout "
                f"after {total_timeout_seconds:.1f}s"
            )

        current_progress = _hls_output_progress(
            segment_dir=segment_pattern.parent,
            playlist_path=playlist_path,
        )
        if current_progress != last_progress:
            last_progress = current_progress
            last_progress_at = now
            continue

        idle_seconds = now - last_progress_at
        if idle_seconds > FFMPEG_OUTPUT_PROGRESS_WATCHDOG_SECONDS:
            raise TimeoutError(
                "FFmpeg HLS output stalled for "
                f"{idle_seconds:.1f}s while waiting for completion"
            )


def get_ffmpeg_thread_count() -> int:
    """
    Determines safe thread allocation for HLS encoding.
    Prioritizes explicit env override, falls back to (CPUs - 2).
    """
    configured_threads = os.environ.get(HLS_FFMPEG_THREADS_ENV)
    if configured_threads is not None:
        try:
            parsed_threads = int(configured_threads)
        except ValueError as exc:
            raise RuntimeError(
                f"{HLS_FFMPEG_THREADS_ENV} must be a positive integer"
            ) from exc
        if parsed_threads < 1:
            raise RuntimeError(f"{HLS_FFMPEG_THREADS_ENV} must be a positive integer")
        return parsed_threads

    cpu_count = os.cpu_count()
    if cpu_count is None:
        return 1
    return max(1, cpu_count - 2)


def _ffmpeg_command(
    *,
    key_info_path: Path,
    segment_pattern: Path,
    playlist_path: Path,
    segment_base_url: str,
    input_arg: str = "pipe:0",
) -> list[str]:
    ffmpeg_executable = ffmpeg_wrapper.resolve_ffmpeg_executable()
    threads = get_ffmpeg_thread_count()
    storage_profile = configured_video_storage_profile()

    if ffmpeg_executable is None:
        raise RuntimeError("ffmpeg executable is not available")
    return [
        ffmpeg_executable,
        "-hide_banner",
        "-y",
        "-i",
        input_arg,
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-codec:v",
        "libx264",
        "-threads",
        str(threads),
        "-preset",
        HLS_VIDEO_PRESET,
        "-profile:v",
        HLS_VIDEO_PROFILE,
        "-crf",
        HLS_VIDEO_CRF,
        "-maxrate",
        str(storage_profile.max_bit_rate_bps),
        "-bufsize",
        str(storage_profile.max_bit_rate_bps * 2),
        "-pix_fmt",
        HLS_VIDEO_PIXEL_FORMAT,
        "-vf",
        STANDARD_VIDEO_ENCODING.filter_chain(),
        "-color_range",
        HLS_VIDEO_COLOR_RANGE,
        "-fps_mode",
        "passthrough",
        "-codec:a",
        HLS_AUDIO_CODEC,
        "-f",
        "hls",
        "-hls_time",
        "4",
        "-hls_playlist_type",
        "vod",
        "-hls_key_info_file",
        str(key_info_path),
        "-hls_base_url",
        segment_base_url,
        "-hls_segment_filename",
        str(segment_pattern),
        str(playlist_path),
    ]


def _local_hls_validation_playlist(
    *,
    playlist_path: Path,
    key_info_path: Path,
) -> Path:
    key_info_lines = key_info_path.read_text(encoding="utf-8").splitlines()
    if len(key_info_lines) != 3:
        raise RuntimeError("HLS key info must contain URI, key path, and IV")
    key_path = ensure_within_protected_root(Path(key_info_lines[1]))
    if not key_path.is_file() or key_path.stat().st_size != HLS_CONTENT_KEY_BYTES:
        raise RuntimeError("HLS profile validation key is missing or invalid")

    rewritten_lines: list[str] = []
    key_rewritten = False
    for line in playlist_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#EXT-X-KEY:"):
            rewritten, replacements = HLS_KEY_URI_PATTERN.subn(
                f'URI="{key_path.resolve().as_uri()}"',
                line,
                count=1,
            )
            if replacements != 1:
                raise RuntimeError("HLS playlist key URI is missing")
            rewritten_lines.append(rewritten)
            key_rewritten = True
            continue
        if not line or line.startswith("#"):
            rewritten_lines.append(line)
            continue

        segment_name = Path(urlsplit(line).path).name
        segment_path = ensure_within_protected_root(playlist_path.parent / segment_name)
        if not segment_path.is_file() or segment_path.stat().st_size <= 0:
            raise RuntimeError(
                f"HLS profile validation segment is missing: {segment_name}"
            )
        rewritten_lines.append(segment_path.resolve().as_uri())

    if not key_rewritten:
        raise RuntimeError("HLS playlist has no encryption key declaration")
    payload = ("\n".join(rewritten_lines) + "\n").encode("utf-8")
    validation_path = playlist_path.with_name(".profile-validation.m3u8")
    atomic_write_file(
        destination=validation_path,
        content=(payload,),
        required_bytes=len(payload),
        file_mode=HLS_TEMP_FILE_MODE,
        dir_mode=HLS_TEMP_DIRECTORY_MODE,
    )
    return validation_path


def _validate_generated_hls_profile(
    *,
    playlist_path: Path,
    key_info_path: Path,
    source_probe: VideoArtifactProbe,
) -> None:
    validation_path = _local_hls_validation_playlist(
        playlist_path=playlist_path,
        key_info_path=key_info_path,
    )
    try:
        output_probe = probe_video_artifact(
            validation_path,
            input_policy=FFprobeInputPolicy.TRUSTED_LOCAL_HLS,
        )
        total_size_bytes = playlist_path.stat().st_size + sum(
            segment.stat().st_size for segment in playlist_path.parent.glob("seg_*.ts")
        )
        output_probe = output_probe.model_copy(
            update={"size_bytes": total_size_bytes},
        )
        evidence = validate_normalized_output(
            source=source_probe,
            output=output_probe,
            profile=configured_video_storage_profile(),
            segments=None,
        )
        logger.info(
            "Validated HLS transcoding profile before publication: "
            "profile=%s playlist=%s segments_bytes=%s",
            evidence.profile_name,
            playlist_path,
            total_size_bytes,
        )
    finally:
        safe_unlink_file(validation_path, missing_ok=True)


def _run_ffmpeg_hls(
    *,
    source: BinaryIO,
    source_file_name: str,
    source_size_bytes: int | None,
    temp_source_dir: Path,
    key_info_path: Path,
    segment_pattern: Path,
    playlist_path: Path,
    segment_base_url: str,
) -> None:
    source_path: Path | None = None
    try:
        prefix = source.read(MP4_PIPE_COMPATIBILITY_SCAN_BYTES)
        source_path = _materialize_seekable_plaintext_source(
            source=source,
            prefix=prefix,
            source_file_name=source_file_name,
            source_size_bytes=source_size_bytes,
            temp_source_dir=temp_source_dir,
        )
        normalization_evidence = normalize_video_file(
            input_path=source_path,
            reference_path=source_path,
            quality_mode=get_ffmpeg_transcode_quality_mode(),
        )
        input_arg = str(source_path)
    except BaseException:
        _cleanup_seekable_plaintext_source(
            temp_source_dir=temp_source_dir,
            source_path=source_path,
        )
        raise

    command = _ffmpeg_command(
        input_arg=input_arg,
        key_info_path=key_info_path,
        segment_pattern=segment_pattern,
        playlist_path=playlist_path,
        segment_base_url=segment_base_url,
    )
    stderr_chunks: deque[bytes] = deque()
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except BaseException:
        _cleanup_seekable_plaintext_source(
            temp_source_dir=temp_source_dir,
            source_path=source_path,
        )
        raise
    stderr_thread: threading.Thread | None = None
    if process.stderr is not None:
        stderr_thread = threading.Thread(
            target=_drain_pipe_tail,
            args=(process.stderr, stderr_chunks),
            daemon=True,
        )
        stderr_thread.start()

    try:
        return_code = _wait_for_ffmpeg_completion(
            process=process,
            segment_pattern=segment_pattern,
            playlist_path=playlist_path,
        )

    except TimeoutError as exc:
        try:
            process.kill()
        finally:
            try:
                return_code = process.wait(timeout=5.0)
            except Exception:
                return_code = -1
        raise RuntimeError(
            "FFmpeg HLS pipeline timed out: "
            f"returncode={return_code} stderr={_stderr_tail(stderr_chunks)}"
        ) from exc
    except (BrokenPipeError, OSError) as exc:
        try:
            process.kill()
        finally:
            try:
                return_code = process.wait(timeout=5.0)
            except Exception:
                return_code = -1
        raise RuntimeError(
            "FFmpeg HLS pipeline terminated while reading source: "
            f"returncode={return_code} stderr={_stderr_tail(stderr_chunks)}"
        ) from exc
    except RuntimeError as exc:
        try:
            process.kill()
        finally:
            try:
                return_code = process.wait(timeout=5.0)
            except Exception:
                return_code = -1
        raise RuntimeError(
            "FFmpeg HLS stdin pipeline failed: "
            f"returncode={return_code} stderr={_stderr_tail(stderr_chunks)}"
        ) from exc
    except BaseException:
        try:
            process.kill()
        finally:
            try:
                process.wait(timeout=5.0)
            except Exception:
                pass
        raise
    finally:
        if stderr_thread is not None:
            stderr_thread.join(timeout=5.0)
        _cleanup_seekable_plaintext_source(
            temp_source_dir=temp_source_dir,
            source_path=source_path,
        )

    if return_code != 0:
        raise RuntimeError(
            "FFmpeg HLS pipeline failed: "
            f"returncode={return_code} stderr={_stderr_tail(stderr_chunks)}"
        )
    _validate_generated_hls_profile(
        playlist_path=playlist_path,
        key_info_path=key_info_path,
        source_probe=normalization_evidence.output,
    )


def _open_field_file(field_file: FieldFile) -> BinaryIO:
    field_file.open("rb")
    return cast(BinaryIO, field_file.file)


@contextmanager
def _open_hls_source(source: _HlsSource) -> Generator[BinaryIO, None, None]:
    field_file = source.field_file
    handle = _open_field_file(field_file)
    try:
        yield handle
    finally:
        field_file.close()


def _ready_artifact_paths_exist(artifact: VideoHlsArtifact) -> bool:
    playlist_path = resolve_existing_protected_media_path(
        artifact.playlist_relative_path
    )
    segment_dir = resolve_existing_protected_media_path(
        artifact.segment_directory_relative_path
    )
    if playlist_path is None or segment_dir is None:
        return False
    if not playlist_path.is_file() or not segment_dir.is_dir():
        return False
    if int(artifact.segment_count) <= 0:
        return False
    return True


def _result_from_ready_artifact(
    artifact: VideoHlsArtifact,
    *,
    status: str,
    detail: str = "",
) -> HlsMaterializationResult:
    return HlsMaterializationResult(
        video_id=int(artifact.video_id),
        artifact_kind=cast(HlsArtifactKind, str(artifact.artifact_kind)),
        status=status,
        key_id=str(artifact.key_id),
        playlist_relative_path=str(artifact.playlist_relative_path),
        segment_directory_relative_path=str(artifact.segment_directory_relative_path),
        segment_count=int(artifact.segment_count),
        detail=detail,
    )


def _existing_ready_result(
    *,
    video_id: int,
    artifact_kind: VideoArtifactKind,
) -> HlsMaterializationResult | None:
    try:
        artifact = VideoHlsArtifact.objects.get(
            video_id=video_id,
            artifact_kind=artifact_kind.value,
        )
    except VideoHlsArtifact.DoesNotExist:
        return None

    if artifact.status != VideoHlsArtifact.Status.READY.value:
        return None
    source_ref = _hls_source(artifact.video, artifact_kind)
    if artifact.source_file_name != source_ref.source_file_name:
        return None
    if not _ready_artifact_paths_exist(artifact):
        _mark_artifact_failed(
            artifact_id=int(artifact.pk),
            error="HLS artifact was marked ready but playlist or segments are missing.",
            previous=None,
            expected_key_id=artifact.key_id,
            expected_status=VideoHlsArtifact.Status.READY.value,
            error_code=VideoHlsArtifact.ErrorCode.INCONSISTENT_ARTIFACT.value,
        )
        raise RuntimeError(
            f"HLS artifact state is inconsistent for video={video_id} "
            f"kind={artifact_kind.value}"
        )
    return _result_from_ready_artifact(artifact, status="already_ready")


def _assert_hls_outputs(
    *,
    playlist_path: Path,
    target_dir: Path,
) -> int:
    if not playlist_path.is_file() or playlist_path.stat().st_size <= 0:
        raise RuntimeError("FFmpeg did not produce a non-empty HLS playlist")
    segments = sorted(target_dir.glob("seg_*.ts"))
    if not segments:
        raise RuntimeError("FFmpeg did not produce HLS segments")
    for segment in segments:
        if not segment.is_file() or segment.stat().st_size <= 0:
            raise RuntimeError(f"FFmpeg produced an invalid HLS segment: {segment}")
        set_path_mode(segment, HLS_FILE_MODE)
    set_path_mode(playlist_path, HLS_FILE_MODE)
    return len(segments)


def _commit_hls_output(
    *,
    temp_output_dir: Path,
    target_dir: Path,
) -> None:
    if not temp_output_dir.is_dir():
        raise RuntimeError("FFmpeg HLS temporary output directory is missing")
    safe_rmtree(target_dir, missing_ok=True)
    ensure_directory(target_dir.parent, dir_mode=HLS_DIRECTORY_MODE)
    atomic_move_path(
        source=temp_output_dir,
        destination=target_dir,
        dir_mode=HLS_DIRECTORY_MODE,
    )
    set_path_mode(target_dir, HLS_DIRECTORY_MODE)


def _iter_path_tree(path: Path) -> Iterable[Path]:
    if not path.exists():
        return ()
    return tuple(path.rglob("*"))


def _cleanup_partial_output(target_dir: Path) -> None:
    safe_rmtree(target_dir, missing_ok=True)
    parent = target_dir.parent
    if parent.exists() and not any(_iter_path_tree(parent)):
        safe_rmtree(parent, missing_ok=True)


def _cleanup_replaced_artifact(snapshot: _ArtifactSnapshot | None) -> None:
    if snapshot is None:
        return

    playlist_path = resolve_existing_protected_media_path(
        snapshot.playlist_relative_path
    )
    segment_dir = resolve_existing_protected_media_path(
        snapshot.segment_directory_relative_path
    )
    if segment_dir is None and playlist_path is None:
        return

    if segment_dir is not None:
        safe_rmtree(segment_dir, missing_ok=True)
        parent = segment_dir.parent
        if parent.exists() and not any(_iter_path_tree(parent)):
            safe_rmtree(parent, missing_ok=True)
        return

    if playlist_path is not None:
        safe_unlink_file(playlist_path, missing_ok=True)


def _cleanup_transient_hls_artifact(*, video_id: int, key_id: UUID) -> None:
    temp_key_dir = _temporary_key_dir(video_id=video_id, key_id=key_id)
    safe_unlink_file(temp_key_dir / "key_info.txt", missing_ok=True)
    secure_unlink_file(temp_key_dir / "hls.key", missing_ok=True)
    safe_rmtree(temp_key_dir, missing_ok=True)

    temp_source_dir = _temporary_plaintext_source_dir(
        video_id=video_id,
        key_id=key_id,
    )
    if temp_source_dir.exists():
        for source_path in temp_source_dir.rglob("*"):
            if source_path.is_file():
                secure_unlink_file(source_path, missing_ok=True)
    safe_rmtree(temp_source_dir, missing_ok=True)

    safe_rmtree(
        _temporary_output_dir(video_id=video_id, key_id=key_id),
        missing_ok=True,
    )


@transaction.atomic
def delete_video_hls_artifacts(
    video: VideoFile,
    *,
    artifact_kind: object,
) -> bool:
    """Delete one video's HLS state and all owned files for an artifact kind."""
    video_pk = getattr(video, "pk", None)
    if not isinstance(video_pk, int):
        raise ValueError("Cannot delete HLS artifacts for an unsaved video.")
    from endoreg_db.services.media_operation_gate import defer_if_video_media_busy

    defer_if_video_media_busy(video_id=video_pk)

    parsed_kind = coerce_hls_artifact_kind(artifact_kind)
    locked_video = VideoFile.objects.select_for_update().get(pk=video_pk)
    artifacts = tuple(
        VideoHlsArtifact.objects.select_for_update().filter(
            video=locked_video,
            artifact_kind=parsed_kind.value,
        )
    )

    video_hls_dir = ensure_within_protected_media_root(
        _hls_root_for_kind(parsed_kind) / str(locked_video.uuid)
    )
    removed = video_hls_dir.exists() or bool(artifacts)
    safe_rmtree(video_hls_dir, missing_ok=True)

    for artifact in artifacts:
        transient_paths = (
            _temporary_key_dir(video_id=int(locked_video.pk), key_id=artifact.key_id),
            _temporary_plaintext_source_dir(
                video_id=int(locked_video.pk),
                key_id=artifact.key_id,
            ),
            _temporary_output_dir(
                video_id=int(locked_video.pk),
                key_id=artifact.key_id,
            ),
        )
        removed = removed or any(path.exists() for path in transient_paths)
        _cleanup_transient_hls_artifact(
            video_id=int(locked_video.pk),
            key_id=artifact.key_id,
        )

    if artifacts:
        VideoHlsArtifact.objects.filter(
            pk__in=[int(artifact.pk) for artifact in artifacts]
        ).delete()

    logger.info(
        "Deleted %s HLS artifacts for video=%s artifact_records=%s removed=%s",
        parsed_kind.value,
        locked_video.pk,
        len(artifacts),
        removed,
    )
    return removed


def materialize_video_hls(
    video_id: int,
    *,
    artifact_kind: object = VideoArtifactKind.PROCESSED,
    force: bool = False,
) -> HlsMaterializationResult:
    parsed_kind = coerce_hls_artifact_kind(artifact_kind)
    if not force:
        existing = _existing_ready_result(video_id=video_id, artifact_kind=parsed_kind)
        if existing is not None:
            return existing

    from endoreg_db.services.media_operation_gate import defer_if_video_media_busy

    defer_if_video_media_busy(video_id=int(video_id))

    video = VideoFile.objects.get(pk=int(video_id))
    source_ref = _hls_source(video, parsed_kind)
    source_file_name = source_ref.source_file_name

    cek = os.urandom(HLS_CONTENT_KEY_BYTES)
    key_id = uuid4()
    iv_hex = os.urandom(HLS_CONTENT_KEY_BYTES).hex()
    if len(iv_hex) != HLS_IV_HEX_LENGTH:
        raise RuntimeError("Generated HLS IV has invalid length")
    key_ciphertext, key_nonce = _wrap_hls_content_key(
        cek=cek,
        video_id=int(video.pk),
        artifact_kind=parsed_kind,
        key_id=key_id,
    )
    prepared = _prepare_artifact_record(
        video_id=int(video.pk),
        artifact_kind=parsed_kind,
        source_file_name=source_file_name,
        key_id=key_id,
        key_ciphertext=key_ciphertext,
        key_nonce=key_nonce,
        iv_hex=iv_hex,
        force=force,
    )

    if not prepared.should_materialize:
        artifact = VideoHlsArtifact.objects.get(pk=prepared.artifact_id)
        if artifact.status == VideoHlsArtifact.Status.READY.value:
            return _result_from_ready_artifact(artifact, status="already_ready")
        return HlsMaterializationResult(
            video_id=int(artifact.video_id),
            artifact_kind=cast(HlsArtifactKind, str(artifact.artifact_kind)),
            status="already_materializing",
            key_id=str(artifact.key_id),
            playlist_relative_path="",
            segment_directory_relative_path="",
            segment_count=0,
            detail="An HLS materialization task is already active.",
        )

    target_dir = _artifact_target_dir(
        video=video,
        artifact_kind=parsed_kind,
        key_id=key_id,
    )
    temp_key_dir = _temporary_key_dir(video_id=int(video.pk), key_id=key_id)
    temp_output_dir = _temporary_output_dir(video_id=int(video.pk), key_id=key_id)
    temp_source_dir = _temporary_plaintext_source_dir(
        video_id=int(video.pk),
        key_id=key_id,
    )
    playlist_path = target_dir / "playlist.m3u8"
    temp_playlist_path = temp_output_dir / "playlist.m3u8"
    temp_segment_pattern = temp_output_dir / "seg_%03d.ts"
    key_uri = build_video_hls_key_path(int(video.pk), str(key_id))
    segment_base_url = build_video_hls_segment_base_path(int(video.pk), str(key_id))

    try:
        safe_rmtree(target_dir, missing_ok=True)
        safe_rmtree(temp_output_dir, missing_ok=True)
        ensure_directory(temp_output_dir, dir_mode=HLS_TEMP_DIRECTORY_MODE)
        with _temporary_hls_key_material(
            temp_key_dir=temp_key_dir,
            cek=cek,
            key_uri=key_uri,
            iv_hex=iv_hex,
        ) as (_, _key_info_path):
            with _open_hls_source(source_ref) as source:
                _run_ffmpeg_hls(
                    source=source,
                    source_file_name=source_ref.source_file_name,
                    source_size_bytes=_source_size_bytes(source_ref.field_file),
                    temp_source_dir=temp_source_dir,
                    key_info_path=_key_info_path,
                    segment_pattern=temp_segment_pattern,
                    playlist_path=temp_playlist_path,
                    segment_base_url=segment_base_url,
                )
        segment_count = _assert_hls_outputs(
            playlist_path=temp_playlist_path,
            target_dir=temp_output_dir,
        )
        _commit_hls_output(
            temp_output_dir=temp_output_dir,
            target_dir=target_dir,
        )
        artifact = _mark_artifact_ready(
            artifact_id=prepared.artifact_id,
            expected_key_id=prepared.key_id,
            playlist_relative_path=to_protected_media_relative(playlist_path),
            segment_directory_relative_path=to_protected_media_relative(target_dir),
            segment_count=segment_count,
        )
        try:
            _cleanup_replaced_artifact(prepared.previous)
        except Exception as cleanup_exc:
            logger.warning(
                "Could not remove replaced HLS artifact after materialization: %s",
                cleanup_exc,
                exc_info=True,
            )
        return _result_from_ready_artifact(artifact, status="materialized")
    except BaseException as exc:
        safe_rmtree(temp_output_dir, missing_ok=True)
        _cleanup_partial_output(target_dir)
        try:
            # Erzeuge eine saubere Fehlermeldung auch für leere SystemExits
            error_msg = (
                f"System termination or unhandled exception: {type(exc).__name__}"
            )
            if str(exc):
                error_msg += f" - {exc}"

            _mark_artifact_failed(
                artifact_id=prepared.artifact_id,
                error=error_msg,
                previous=prepared.previous,
                expected_key_id=prepared.key_id,
                expected_status=VideoHlsArtifact.Status.MATERIALIZING.value,
            )
        except Exception as mark_exc:
            logger.warning(
                "Failed to persist failed state for HLS artifact %s: %s",
                prepared.artifact_id,
                mark_exc,
                exc_info=True,
            )
        raise
    finally:
        safe_rmtree(temp_output_dir, missing_ok=True)


def get_ready_hls_artifact(
    *,
    video: VideoFile,
    artifact_kind: object = VideoArtifactKind.PROCESSED,
    key_id: UUID | None = None,
) -> VideoHlsArtifact:
    parsed_kind = coerce_hls_artifact_kind(artifact_kind)
    filters: dict[str, object] = {
        "video": video,
        "artifact_kind": parsed_kind.value,
        "status": VideoHlsArtifact.Status.READY.value,
    }
    if key_id is not None:
        filters["key_id"] = key_id
    artifact = VideoHlsArtifact.objects.get(**filters)
    if not _ready_artifact_paths_exist(artifact):
        raise FileNotFoundError("HLS artifact files are missing")
    return artifact


def get_ready_hls_artifact_by_key(
    *,
    video: VideoFile,
    key_id: UUID,
) -> VideoHlsArtifact:
    artifact = VideoHlsArtifact.objects.get(
        video=video,
        key_id=key_id,
        status=VideoHlsArtifact.Status.READY.value,
    )

    if not _ready_artifact_paths_exist(artifact):
        raise FileNotFoundError("HLS artifact files are missing")
    return artifact


def hls_playlist_path(artifact: VideoHlsArtifact) -> Path:
    playlist_path = resolve_existing_protected_media_path(
        artifact.playlist_relative_path
    )
    if playlist_path is None or not playlist_path.is_file():
        raise FileNotFoundError("HLS playlist is not available")
    return playlist_path


def hls_segment_path(artifact: VideoHlsArtifact, segment_name: str) -> Path:
    normalized = str(segment_name).strip()
    if "/" in normalized or "\\" in normalized or normalized != Path(normalized).name:
        raise ValueError("Invalid HLS segment name")
    if not normalized.startswith("seg_") or Path(normalized).suffix != ".ts":
        raise ValueError("Invalid HLS segment name")

    segment_dir = resolve_existing_protected_media_path(
        artifact.segment_directory_relative_path
    )
    if segment_dir is None or not segment_dir.is_dir():
        raise FileNotFoundError("HLS segment directory is not available")

    target = (segment_dir / normalized).resolve(strict=True)
    try:
        target.relative_to(segment_dir.resolve(strict=True))
    except ValueError as exc:
        raise ValueError("Invalid HLS segment path") from exc
    if not target.is_file():
        raise FileNotFoundError("HLS segment is not available")
    return target

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from endoreg_db.services.streamable_media_types import (
    DEFAULT_STREAMABLE_TRANSCODE_PROFILE,
    MP4_SUFFIX,
    STREAMABLE_DIRECTORY_MODE,
    STREAMABLE_FILE_MODE,
    StreamableTranscodeProfile,
)
from endoreg_db.utils import ffmpeg_wrapper
from endoreg_db.utils.encryption.encrypted import MAGIC as LX_ENCRYPTED_MAGIC
from endoreg_db.utils.file_operations import (
    atomic_move_file,
    ensure_directory,
    safe_unlink_file,
)
from endoreg_db.utils.rust_backend import is_lx_encrypted_file


def is_encrypted_file(path: Path) -> bool:
    rust_result = is_lx_encrypted_file(path)
    if rust_result is not None:
        return rust_result
    with path.open("rb") as handle:
        return handle.read(len(LX_ENCRYPTED_MAGIC)) == LX_ENCRYPTED_MAGIC


def _first_atom_offset(path: Path, atom: bytes, *, scan_bytes: int = 64 * 1024) -> int:
    with path.open("rb") as handle:
        data = handle.read(scan_bytes)
    return data.find(atom)


def is_faststart_mp4(path: Path) -> bool:
    if path.suffix.lower() != MP4_SUFFIX:
        return False
    ftyp_offset = _first_atom_offset(path, b"ftyp")
    moov_offset = _first_atom_offset(path, b"moov")
    mdat_offset = _first_atom_offset(path, b"mdat")
    return (
        ftyp_offset >= 0
        and moov_offset >= 0
        and (mdat_offset < 0 or moov_offset < mdat_offset)
    )


def transcode_streamable_mp4(
    source_path: Path,
    target_path: Path,
    *,
    profile: StreamableTranscodeProfile = DEFAULT_STREAMABLE_TRANSCODE_PROFILE,
) -> Path:
    ensure_directory(target_path.parent, dir_mode=STREAMABLE_DIRECTORY_MODE)
    temp_target = target_path.with_name(
        f".{target_path.stem}.ffmpeg.{os.getpid()}.{uuid4().hex}.tmp{MP4_SUFFIX}"
    )
    try:
        if is_encrypted_file(source_path):
            raise RuntimeError(f"Refusing encrypted streamable source: {target_path}")

        result = ffmpeg_wrapper.transcode_video(
            input_path=source_path,
            output_path=temp_target,
            codec=profile.codec,
            crf=profile.crf,
            preset=profile.preset,
            audio_codec=profile.audio_codec,
            audio_bitrate=profile.audio_bitrate,
            extra_args=profile.extra_args(),
            quality_mode="fast",
            force_cpu=True,
        )
        if result is None:
            raise RuntimeError(
                f"ffmpeg failed to create streamable MP4 artifact {target_path}"
            )
        if Path(result) != temp_target:
            raise RuntimeError(
                f"ffmpeg returned an unexpected streamable artifact path: {result}"
            )
        if is_encrypted_file(temp_target):
            raise RuntimeError(f"Refusing encrypted streamable artifact: {target_path}")
        if not is_faststart_mp4(temp_target):
            raise RuntimeError(
                f"Refusing streamable artifact without front-loaded MP4 metadata: {target_path}"
            )
        return atomic_move_file(
            source=temp_target,
            destination=target_path,
            file_mode=STREAMABLE_FILE_MODE,
            dir_mode=STREAMABLE_DIRECTORY_MODE,
        )
    finally:
        safe_unlink_file(temp_target, missing_ok=True)

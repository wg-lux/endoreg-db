# pyright: reportPrivateUsage=false
from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Iterator, TypedDict, cast

import numpy as np

from endoreg_db.services.streaming.seekable_media_input import (
    SeekableFieldFile,
    serve_seekable_media_input,
)
from endoreg_db.utils.ffmpeg_wrapper import (
    _resolve_ffmpeg_executable,
    get_stream_info,
)
from endoreg_db.utils.storage_streaming import (
    field_file_has_decrypted_range_storage,
    maybe_local_plaintext_path,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

from endoreg_db.services.video_storage.probes import probe_video_frame_pts


class _VideoStreamInfo(TypedDict):
    codec_type: str
    width: int
    height: int
    avg_frame_rate: str | None
    r_frame_rate: str | None


class _FfprobeStreamInfo(TypedDict):
    streams: list[_VideoStreamInfo]


@dataclass(frozen=True)
class FrameSample:
    frame_number: int
    timestamp: float
    rgb_frame: np.ndarray


@dataclass(frozen=True)
class EncodedFrameSample:
    frame_number: int
    timestamp: float
    content_type: str
    image_bytes: bytes


def _video_stream_metadata(video_path: Path) -> tuple[int, int]:
    payload = get_stream_info(video_path)
    if payload is None:
        raise RuntimeError("ffprobe returned no streams.")
    streams = cast(_FfprobeStreamInfo, payload)
    for stream in streams["streams"]:
        if stream["codec_type"] == "video":
            width, height = int(stream["width"]), int(stream["height"])
            if width <= 0 or height <= 0:
                raise ValueError("Video dimensions must be positive.")
            return width, height
    raise RuntimeError("ffprobe returned no video stream.")


def _read_exact(handle: IO[bytes], byte_count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = byte_count
    while remaining > 0:
        chunk = handle.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def iter_video_path_frame_samples(
    video_path: Path,
) -> Iterator[FrameSample]:
    ffmpeg_executable = _resolve_ffmpeg_executable()
    if not ffmpeg_executable:
        raise FileNotFoundError(
            "ffmpeg command not found. Ensure FFmpeg is installed and in PATH."
        )

    width, height = _video_stream_metadata(video_path)
    timestamps = probe_video_frame_pts(video_path)
    frame_size = width * height * 3
    command = [
        ffmpeg_executable,
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(video_path),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-fps_mode",
        "passthrough",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    stdout = process.stdout
    frame_number = 0
    try:
        while True:
            frame_bytes = _read_exact(stdout, frame_size)
            if not frame_bytes:
                break
            if len(frame_bytes) != frame_size:
                raise RuntimeError(
                    "ffmpeg produced a partial raw frame "
                    f"for {video_path.name}: expected={frame_size} actual={len(frame_bytes)}."
                )
            frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(
                (height, width, 3)
            )
            if frame_number >= len(timestamps):
                raise RuntimeError("Decoded frame count exceeds observed timestamps.")
            yield FrameSample(
                frame_number=frame_number,
                timestamp=timestamps[frame_number],
                rgb_frame=frame,
            )
            frame_number += 1

        return_code = process.wait()
        if return_code != 0:
            stderr_pipe = process.stderr
            stderr = (
                stderr_pipe.read().decode("utf-8", errors="replace")
                if stderr_pipe is not None
                else ""
            )
            raise RuntimeError(
                f"ffmpeg streaming decode failed for {video_path.name} "
                f"with exit code {return_code}: {stderr.strip()}"
            )
        if frame_number != len(timestamps):
            raise RuntimeError("Decoded frame count differs from observed timestamps.")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        assert process.stdout is not None
        assert process.stderr is not None
        process.stdout.close()
        process.stderr.close()


def read_video_path_frame_jpeg(
    video_path: Path,
    *,
    frame_number: int,
    timestamp: float,
    quality: int = 2,
) -> EncodedFrameSample:
    if frame_number < 0:
        raise ValueError("frame_number must be non-negative.")

    if quality < 1 or quality > 31:
        raise ValueError("quality must be between 1 and 31 for ffmpeg mjpeg output.")

    return _read_video_input_frame_jpeg(
        str(video_path),
        frame_number=frame_number,
        timestamp=timestamp,
        quality=quality,
        seekable_http=False,
    )


def _read_video_input_frame_jpeg(
    input_reference: str,
    *,
    frame_number: int,
    timestamp: float,
    quality: int,
    seekable_http: bool,
) -> EncodedFrameSample:
    if frame_number < 0:
        raise ValueError("frame_number must be non-negative.")
    if not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError("timestamp must be finite and non-negative.")
    if quality < 1 or quality > 31:
        raise ValueError("quality must be between 1 and 31 for ffmpeg mjpeg output.")

    ffmpeg_executable = _resolve_ffmpeg_executable()
    if not ffmpeg_executable:
        raise FileNotFoundError(
            "ffmpeg command not found. Ensure FFmpeg is installed and in PATH."
        )

    command = [
        ffmpeg_executable,
        "-nostdin",
        "-v",
        "error",
        "-seek_timestamp",
        "1",
        "-ss",
        f"{timestamp:.9f}",
    ]
    if seekable_http:
        command.extend(["-seekable", "1"])
    command.extend(
        [
            "-i",
            input_reference,
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-frames:v",
            "1",
            "-q:v",
            str(quality),
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ]
    )
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr_bytes = process.communicate()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
    if process.returncode != 0:
        raise RuntimeError(
            "ffmpeg single-frame jpeg decode failed "
            f"with exit code {process.returncode}: {stderr.strip()}"
        )
    if not stdout:
        raise RuntimeError(f"ffmpeg produced no encoded frame {frame_number}.")

    return EncodedFrameSample(
        frame_number=frame_number,
        timestamp=timestamp,
        content_type="image/jpeg",
        image_bytes=stdout,
    )


def iter_video_file_frame_samples(
    video: VideoFile,
    *,
    file_type: str = "raw",
) -> Iterator[FrameSample]:
    from endoreg_db.services.video_files.frame_coordinates import (
        artifact_frame_timestamps,
    )

    if file_type.strip().lower() not in {"raw", "processed"}:
        raise ValueError("Unsupported video file type.")
    materialize = (
        video.ensure_local_raw_file
        if file_type.strip().lower() == "raw"
        else video.ensure_local_processed_file
    )
    with materialize() as source_path:
        timestamps = artifact_frame_timestamps(
            video, file_type=file_type, source_path=source_path
        )
        decoded_count = 0
        for sample in iter_video_path_frame_samples(source_path):
            if sample.frame_number >= len(timestamps) or not math.isclose(
                sample.timestamp,
                timestamps[sample.frame_number],
                abs_tol=1e-6,
                rel_tol=0,
            ):
                raise ValueError(
                    "Decoded frame differs from the selected canonical timeline."
                )
            decoded_count += 1
            yield FrameSample(
                sample.frame_number, timestamps[sample.frame_number], sample.rgb_frame
            )

        if decoded_count != len(timestamps):
            raise ValueError("Decoded frame count differs from the canonical timeline.")


def read_video_file_frame_jpeg(
    video: VideoFile,
    *,
    frame_number: int,
    file_type: str = "raw",
    quality: int = 2,
) -> EncodedFrameSample:
    normalized_type = file_type.strip().lower()
    if normalized_type == "raw":
        field_file = video.raw_file
    elif normalized_type == "processed":
        field_file = video.processed_file
    else:
        raise ValueError(f"Unsupported video file type: {file_type}")

    field_file_name = getattr(field_file, "name", None)
    if not isinstance(field_file_name, str) or not field_file_name:
        raise FileNotFoundError(
            f"{normalized_type.title()} video file is not available for "
            f"{video.raw_video_hash}."
        )

    from endoreg_db.services.video_files.frame_coordinates import (
        artifact_frame_timestamps,
    )

    timestamps = artifact_frame_timestamps(video, file_type=normalized_type)
    if frame_number < 0 or frame_number >= len(timestamps):
        raise ValueError("Frame number is outside the selected canonical timeline.")
    timestamp = timestamps[frame_number]
    local_path = maybe_local_plaintext_path(field_file)
    if local_path is not None:
        return read_video_path_frame_jpeg(
            local_path,
            frame_number=frame_number,
            timestamp=timestamp,
            quality=quality,
        )

    if not field_file_has_decrypted_range_storage(field_file):
        raise RuntimeError(
            "Video storage cannot provide seekable decrypted byte ranges."
        )

    with serve_seekable_media_input(cast(SeekableFieldFile, field_file)) as source:
        return _read_video_input_frame_jpeg(
            source.url,
            frame_number=frame_number,
            timestamp=timestamp,
            quality=quality,
            seekable_http=True,
        )


__all__ = [
    "EncodedFrameSample",
    "FrameSample",
    "iter_video_file_frame_samples",
    "iter_video_path_frame_samples",
    "read_video_file_frame_jpeg",
    "read_video_path_frame_jpeg",
]

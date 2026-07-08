# pyright: reportPrivateUsage=false
from __future__ import annotations

import logging
import subprocess
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Callable, Iterator, Protocol, TypedDict, cast

import numpy as np

from endoreg_db.config.env import DEFAULT_VIDEO_FPS
from endoreg_db.utils.ffmpeg_wrapper import (
    _resolve_ffmpeg_executable,
    get_stream_info,
)
from endoreg_db.utils.storage import materialize_video_file as _materialize_video_file

logger = logging.getLogger(__name__)


class _VideoStreamInfo(TypedDict):
    codec_type: str
    width: int
    height: int
    avg_frame_rate: str | None
    r_frame_rate: str | None


class _FfprobeStreamInfo(TypedDict):
    streams: list[_VideoStreamInfo]


class _VideoLike(Protocol):
    video_hash: str

    def ensure_local_raw_file(self) -> AbstractContextManager[Path]: ...

    def ensure_local_processed_file(self) -> AbstractContextManager[Path]: ...

    raw_file: object
    processed_file: object

    def get_fps(self) -> float | int | str | None: ...


def _materialize_video_file_typed(
    video: _VideoLike,
    file_type: str,
) -> AbstractContextManager[Path]:
    helper = cast(
        Callable[[_VideoLike, str], AbstractContextManager[Path]],
        _materialize_video_file,
    )
    return helper(video, file_type)


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


def _parse_frame_rate(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "0/0":
        return None
    if "/" in text:
        numerator_text, denominator_text = text.split("/", 1)
        try:
            numerator = float(numerator_text)
            denominator = float(denominator_text)
        except ValueError:
            return None
        if denominator == 0:
            return None
        return numerator / denominator
    try:
        return float(text)
    except ValueError:
        return None


def _video_stream_metadata(
    video_path: Path,
    *,
    fps_hint: float | None,
) -> tuple[int, int, float]:
    stream_info_payload = get_stream_info(video_path)
    if stream_info_payload is None:
        raise RuntimeError(f"ffprobe returned no streams for {video_path.name}.")
    stream_info = cast(_FfprobeStreamInfo, stream_info_payload)
    video_stream = next(
        (
            stream
            for stream in stream_info["streams"]
            if stream["codec_type"] == "video"
        ),
        None,
    )
    if video_stream is None:
        raise RuntimeError(f"ffprobe returned no video stream for {video_path.name}.")
    width = int(video_stream["width"])
    height = int(video_stream["height"])
    fps = (
        fps_hint
        or _parse_frame_rate(video_stream["avg_frame_rate"])
        or _parse_frame_rate(video_stream["r_frame_rate"])
        or DEFAULT_VIDEO_FPS
    )
    if fps <= 0:
        fps = DEFAULT_VIDEO_FPS
    return width, height, float(fps)


def _video_stream_fps(
    video_path: Path,
    *,
    fps_hint: float | None,
) -> float:
    if fps_hint is not None and fps_hint > 0:
        return float(fps_hint)

    _width, _height, fps = _video_stream_metadata(video_path, fps_hint=fps_hint)
    return fps


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
    *,
    fps_hint: float | None = None,
) -> Iterator[FrameSample]:
    ffmpeg_executable = _resolve_ffmpeg_executable()
    if not ffmpeg_executable:
        raise FileNotFoundError(
            "ffmpeg command not found. Ensure FFmpeg is installed and in PATH."
        )

    width, height, fps = _video_stream_metadata(video_path, fps_hint=fps_hint)
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
            yield FrameSample(
                frame_number=frame_number,
                timestamp=frame_number / fps,
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


def read_video_path_frame_sample(
    video_path: Path,
    *,
    frame_number: int,
    fps_hint: float | None = None,
) -> FrameSample:
    if frame_number < 0:
        raise ValueError("frame_number must be non-negative.")

    ffmpeg_executable = _resolve_ffmpeg_executable()
    if not ffmpeg_executable:
        raise FileNotFoundError(
            "ffmpeg command not found. Ensure FFmpeg is installed and in PATH."
        )

    width, height, fps = _video_stream_metadata(video_path, fps_hint=fps_hint)
    frame_size = width * height * 3
    select_filter = f"select='eq(n,{frame_number})'"
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
        "-vf",
        select_filter,
        "-vsync",
        "vfr",
        "-frames:v",
        "1",
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
    try:
        frame_bytes = _read_exact(stdout, frame_size)
        return_code = process.wait()
        stderr_pipe = process.stderr
        stderr = (
            stderr_pipe.read().decode("utf-8", errors="replace")
            if stderr_pipe is not None
            else ""
        )

        if return_code != 0:
            raise RuntimeError(
                f"ffmpeg single-frame decode failed for {video_path.name} "
                f"with exit code {return_code}: {stderr.strip()}"
            )
        if not frame_bytes:
            raise RuntimeError(
                f"ffmpeg produced no decoded frame {frame_number} for {video_path.name}."
            )
        if len(frame_bytes) != frame_size:
            raise RuntimeError(
                "ffmpeg produced a partial decoded frame "
                f"for {video_path.name}: expected={frame_size} actual={len(frame_bytes)}."
            )

        frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape((height, width, 3))
        return FrameSample(
            frame_number=frame_number,
            timestamp=frame_number / fps,
            rgb_frame=frame,
        )
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
    fps_hint: float | None = None,
    quality: int = 2,
) -> EncodedFrameSample:
    if frame_number < 0:
        raise ValueError("frame_number must be non-negative.")

    if quality < 1 or quality > 31:
        raise ValueError("quality must be between 1 and 31 for ffmpeg mjpeg output.")

    ffmpeg_executable = _resolve_ffmpeg_executable()
    if not ffmpeg_executable:
        raise FileNotFoundError(
            "ffmpeg command not found. Ensure FFmpeg is installed and in PATH."
        )

    fps = _video_stream_fps(video_path, fps_hint=fps_hint)
    timestamp = frame_number / fps
    command = [
        ffmpeg_executable,
        "-nostdin",
        "-v",
        "error",
        "-ss",
        f"{timestamp:.6f}",
        "-i",
        str(video_path),
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
            f"ffmpeg single-frame jpeg decode failed for {video_path.name} "
            f"with exit code {process.returncode}: {stderr.strip()}"
        )
    if not stdout:
        raise RuntimeError(
            f"ffmpeg produced no encoded frame {frame_number} for {video_path.name}."
        )

    return EncodedFrameSample(
        frame_number=frame_number,
        timestamp=timestamp,
        content_type="image/jpeg",
        image_bytes=stdout,
    )


def iter_video_file_frame_samples(
    video: _VideoLike,
    *,
    file_type: str = "raw",
) -> Iterator[FrameSample]:
    fps_hint = None
    try:
        fps_value = video.get_fps()
        if isinstance(fps_value, (int, float)):
            fps_hint = float(fps_value) or None
        elif isinstance(fps_value, str):
            fps_hint = float(fps_value) or None
    except (TypeError, ValueError):
        fps_hint = None

    with _materialize_video_file_typed(video, file_type) as source_path:
        yield from iter_video_path_frame_samples(source_path, fps_hint=fps_hint)


def read_video_file_frame_sample(
    video: _VideoLike,
    *,
    frame_number: int,
    file_type: str = "raw",
) -> FrameSample:
    fps_hint: float | None = None
    try:
        fps_value = video.get_fps()
        if isinstance(fps_value, (int, float)):
            fps_hint = float(fps_value) or None
        elif isinstance(fps_value, str):
            fps_hint = float(fps_value) or None
    except (TypeError, ValueError):
        fps_hint = None

    with _materialize_video_file_typed(video, file_type) as source_path:
        return read_video_path_frame_sample(
            source_path,
            frame_number=frame_number,
            fps_hint=fps_hint,
        )


def read_video_file_frame_jpeg(
    video: _VideoLike,
    *,
    frame_number: int,
    file_type: str = "raw",
    quality: int = 2,
) -> EncodedFrameSample:
    fps_hint: float | None = None
    try:
        fps_value = video.get_fps()
        if isinstance(fps_value, (int, float)):
            fps_hint = float(fps_value) or None
        elif isinstance(fps_value, str):
            fps_hint = float(fps_value) or None
    except (TypeError, ValueError):
        fps_hint = None

    with _materialize_video_file_typed(video, file_type) as source_path:
        return read_video_path_frame_jpeg(
            source_path,
            frame_number=frame_number,
            fps_hint=fps_hint,
            quality=quality,
        )


__all__ = [
    "EncodedFrameSample",
    "FrameSample",
    "iter_video_file_frame_samples",
    "iter_video_path_frame_samples",
    "read_video_file_frame_jpeg",
    "read_video_file_frame_sample",
    "read_video_path_frame_jpeg",
    "read_video_path_frame_sample",
]

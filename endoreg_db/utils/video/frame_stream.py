from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterator

import numpy as np

from endoreg_db.config.env import DEFAULT_VIDEO_FPS
from endoreg_db.utils.storage import materialize_video_file
from endoreg_db.utils.video.ffmpeg_wrapper import (
    _resolve_ffmpeg_executable,
    get_stream_info,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameSample:
    frame_number: int
    timestamp: float
    rgb_frame: np.ndarray


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
    stream_info = get_stream_info(video_path)
    if not stream_info or "streams" not in stream_info:
        raise RuntimeError(f"ffprobe returned no streams for {video_path.name}.")
    video_stream = next(
        (
            stream
            for stream in stream_info.get("streams", [])
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    if video_stream is None:
        raise RuntimeError(f"ffprobe returned no video stream for {video_path.name}.")
    try:
        width = int(video_stream["width"])
        height = int(video_stream["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"ffprobe returned invalid frame dimensions for {video_path.name}."
        ) from exc
    fps = (
        fps_hint
        or _parse_frame_rate(video_stream.get("avg_frame_rate"))
        or _parse_frame_rate(video_stream.get("r_frame_rate"))
        or DEFAULT_VIDEO_FPS
    )
    if fps <= 0:
        fps = DEFAULT_VIDEO_FPS
    return width, height, float(fps)


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
    frame_number = 0
    try:
        while True:
            frame_bytes = _read_exact(process.stdout, frame_size)
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
            stderr = ""
            if process.stderr is not None:
                stderr = process.stderr.read().decode("utf-8", errors="replace")
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
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
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
    try:
        frame_bytes = _read_exact(process.stdout, frame_size)
        return_code = process.wait()
        stderr = ""
        if process.stderr is not None:
            stderr = process.stderr.read().decode("utf-8", errors="replace")

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
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def iter_video_file_frame_samples(
    video,
    *,
    file_type: str = "raw",
) -> Iterator[FrameSample]:
    fps_hint = None
    get_fps = getattr(video, "get_fps", None)
    if callable(get_fps):
        try:
            fps_hint = float(get_fps() or 0.0) or None
        except (TypeError, ValueError):
            fps_hint = None

    with materialize_video_file(video, file_type) as source_path:
        yield from iter_video_path_frame_samples(source_path, fps_hint=fps_hint)


def read_video_file_frame_sample(
    video,
    *,
    frame_number: int,
    file_type: str = "raw",
) -> FrameSample:
    fps_hint = None
    get_fps = getattr(video, "get_fps", None)
    if callable(get_fps):
        try:
            fps_hint = float(get_fps() or 0.0) or None
        except (TypeError, ValueError):
            fps_hint = None

    with materialize_video_file(video, file_type) as source_path:
        return read_video_path_frame_sample(
            source_path,
            frame_number=frame_number,
            fps_hint=fps_hint,
        )


__all__ = [
    "FrameSample",
    "iter_video_file_frame_samples",
    "iter_video_path_frame_samples",
    "read_video_file_frame_sample",
    "read_video_path_frame_sample",
]

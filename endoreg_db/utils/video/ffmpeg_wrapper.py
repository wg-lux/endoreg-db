"""Compatibility facade for the legacy video FFmpeg import path.

Canonical FFmpeg helpers live in ``endoreg_db.utils.ffmpeg_wrapper``.
The streamed selected-frame helper remains here for existing annotation export
callers until it is migrated into the focused frame-extraction module.
"""

# pyright: reportPrivateUsage=false

import logging
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from endoreg_db.utils.file_operations import ensure_directory

from ..ffmpeg_wrapper import *  # noqa: F403
from ..ffmpeg_wrapper import (
    __all__ as _CANONICAL_EXPORTS,
    _resolve_ffmpeg_executable,
    get_stream_info,
)

logger = logging.getLogger("ffmpeg_wrapper")


def extract_selected_frames_streamed(
    video_path: Path,
    output_dir: Path,
    *,
    frame_numbers: list[int],
    quality: int,
    ext: str = "jpg",
    fps: Optional[float] = None,
) -> dict[int, Path]:
    """
    Extract selected sampled frame numbers in one FFmpeg pass.

    This avoids:
    - writing every temporary sampled frame to disk
    - huge FFmpeg select expressions
    - repeated decoding from batching

    Correctness contract:
    - FFmpeg still applies the same fps=<fps> filter as the old code.
    - Python enumerates emitted frames as 0, 1, 2, ...
    - That index is matched to DB Frame.frame_number, exactly like the old
      frame_%07d filename mapping.
    """

    ffmpeg_executable = _resolve_ffmpeg_executable()
    if not ffmpeg_executable:
        error_msg = (
            "ffmpeg command not found. Ensure FFmpeg is installed and in the PATH."
        )
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)

    ensure_directory(output_dir)

    requested_numbers = sorted({int(n) for n in frame_numbers if int(n) >= 0})
    if not requested_numbers:
        return {}

    requested_set = set(requested_numbers)
    max_requested = requested_numbers[-1]

    stream_info = get_stream_info(video_path)
    if not stream_info or "streams" not in stream_info:
        raise RuntimeError(f"Could not read video stream info for {video_path}")

    video_stream = next(
        (s for s in stream_info["streams"] if s.get("codec_type") == "video"),
        None,
    )
    if not video_stream:
        raise RuntimeError(f"No video stream found in {video_path}")

    width = int(video_stream["width"])
    height = int(video_stream["height"])
    frame_size = width * height * 3

    filters: list[str] = []
    if fps is not None:
        filters.append(f"fps={fps}")
    filters.append("format=rgb24")

    cmd = [
        ffmpeg_executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vf",
        ",".join(filters),
        "-an",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]

    logger.info(
        "Running streamed selected frame extraction: video=%s requested=%s "
        "max_requested_frame_number=%s fps=%s size=%sx%s",
        video_path,
        len(requested_numbers),
        max_requested,
        fps,
        width,
        height,
    )

    print(
        "[FFMPEG STREAM EXTRACTION] "
        f"video={video_path} requested={len(requested_numbers)} "
        f"max_frame_number={max_requested} fps={fps}",
        flush=True,
    )

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=10**8,
    )

    if process.stdout is None:
        raise RuntimeError("FFmpeg stdout pipe was not created")

    extracted: dict[int, Path] = {}
    sampled_index = 0

    try:
        while True:
            raw = process.stdout.read(frame_size)

            if not raw:
                break

            if len(raw) != frame_size:
                raise RuntimeError(
                    f"Incomplete raw frame from FFmpeg for {video_path}: "
                    f"expected={frame_size}, got={len(raw)}"
                )

            if sampled_index in requested_set:
                arr = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))
                img = Image.fromarray(arr, mode="RGB")

                output_path = output_dir / f"selected_{sampled_index:07d}.{ext}"
                save_kwargs = {}

                if ext.lower() in {"jpg", "jpeg"}:
                    save_kwargs["quality"] = max(
                        1, min(95, int((32 - quality) / 31 * 95))
                    )
                    save_kwargs["subsampling"] = 0

                img.save(output_path, **save_kwargs)
                extracted[sampled_index] = output_path

                if len(extracted) == len(requested_set):
                    # We can stop once the largest requested sampled frame was written.
                    process.kill()
                    break

            sampled_index += 1

            if sampled_index > max_requested and len(extracted) == len(requested_set):
                process.kill()
                break

        stderr = (
            process.stderr.read().decode("utf-8", errors="replace")
            if process.stderr
            else ""
        )
        return_code = process.wait()

        if return_code not in (0, -9) and len(extracted) != len(requested_set):
            logger.error("FFmpeg streamed extraction failed:\n%s", stderr)
            raise RuntimeError(
                f"FFmpeg streamed extraction failed for {video_path}: {stderr}"
            )

    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    missing = sorted(requested_set - set(extracted))
    if missing:
        raise RuntimeError(
            f"Streamed extraction missing {len(missing)} requested frames for "
            f"{video_path}. First missing: {missing[:20]}"
        )

    print(
        "[FFMPEG STREAM EXTRACTION] "
        f"finished extracted={len(extracted)} requested={len(requested_set)}",
        flush=True,
    )

    return extracted


__all__ = [*_CANONICAL_EXPORTS, "extract_selected_frames_streamed"]

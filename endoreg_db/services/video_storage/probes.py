from __future__ import annotations

import json
import math
import subprocess
from fractions import Fraction
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from endoreg_db.schemas.video_storage import (
    FramePresentationTimestamp,
    VideoArtifactProbe,
    VideoTimelineContract,
)
from endoreg_db.services.video_storage.contracts import (
    VideoStorageNormalizationError,
)
from endoreg_db.utils import ffmpeg_wrapper
from endoreg_db.utils.video.command_construction import FFprobeInputPolicy


class _ProbeStream(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    codec_type: str
    codec_name: str | None = None
    pix_fmt: str | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    duration: str | None = None
    avg_frame_rate: str | None = None
    r_frame_rate: str | None = None
    time_base: str | None = None
    nb_frames: str | None = None
    bit_rate: str | None = None


class _ProbeFormat(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    duration: str | None = None
    bit_rate: str | None = None


class _ProbePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    streams: list[_ProbeStream]
    format: _ProbeFormat | None = None


class _FrameProbeRow(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    best_effort_timestamp_time: str | None = None
    best_effort_timestamp: int | str | None = None


class _FrameProbePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    frames: list[_FrameProbeRow]


def _positive_float(value: str | None, *, field_name: str) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = float(value)
    except ValueError as exc:
        raise VideoStorageNormalizationError(
            f"Invalid {field_name} value from ffprobe: {value!r}"
        ) from exc
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def _positive_int(value: str | None, *, field_name: str) -> int | None:
    if value is None or not value.strip() or value.upper() == "N/A":
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise VideoStorageNormalizationError(
            f"Invalid {field_name} value from ffprobe: {value!r}"
        ) from exc
    return parsed if parsed > 0 else None


def _positive_ratio(value: str | None, *, field_name: str) -> Fraction | None:
    if value is None or not value.strip() or value == "0/0":
        return None
    try:
        parsed = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise VideoStorageNormalizationError(
            f"Invalid {field_name} ratio from ffprobe: {value!r}"
        ) from exc
    return parsed if parsed > 0 else None


def probe_video_artifact(
    path: Path,
    *,
    input_policy: FFprobeInputPolicy = FFprobeInputPolicy.DEFAULT,
) -> VideoArtifactProbe:
    candidate = Path(path)
    if not candidate.is_file():
        raise VideoStorageNormalizationError(f"Video artifact is missing: {candidate}")
    size_bytes = candidate.stat().st_size
    if size_bytes <= 0:
        raise VideoStorageNormalizationError(f"Video artifact is empty: {candidate}")

    raw_payload = ffmpeg_wrapper.get_stream_info(
        candidate,
        input_policy=input_policy,
    )
    if raw_payload is None:
        raise VideoStorageNormalizationError(f"ffprobe failed for video: {candidate}")
    payload = _ProbePayload.model_validate(raw_payload)
    stream = next(
        (item for item in payload.streams if item.codec_type == "video"),
        None,
    )
    if stream is None:
        raise VideoStorageNormalizationError(
            f"ffprobe found no video stream: {candidate}"
        )
    if stream.width is None or stream.height is None:
        raise VideoStorageNormalizationError(
            f"ffprobe did not report video dimensions: {candidate}"
        )
    if not stream.codec_name or not stream.pix_fmt:
        raise VideoStorageNormalizationError(
            f"ffprobe did not report codec and pixel format: {candidate}"
        )

    average_rate = _positive_ratio(stream.avg_frame_rate, field_name="avg_frame_rate")
    nominal_rate = _positive_ratio(stream.r_frame_rate, field_name="r_frame_rate")
    frame_rate = average_rate or nominal_rate
    if frame_rate is None:
        raise VideoStorageNormalizationError(
            f"ffprobe did not report a positive frame rate: {candidate}"
        )
    variable_frame_rate = bool(
        average_rate is not None
        and nominal_rate is not None
        and not math.isclose(
            float(average_rate),
            float(nominal_rate),
            rel_tol=0.001,
            abs_tol=0.001,
        )
    )

    duration = _positive_float(stream.duration, field_name="duration")
    if duration is None and payload.format is not None:
        duration = _positive_float(payload.format.duration, field_name="duration")
    if duration is None:
        raise VideoStorageNormalizationError(
            f"ffprobe did not report a positive duration: {candidate}"
        )
    frame_count = _positive_int(stream.nb_frames, field_name="nb_frames")
    if frame_count is None:
        frame_count = max(1, round(duration * float(frame_rate)))
    time_base = _positive_ratio(stream.time_base, field_name="time_base")
    bit_rate = _positive_int(stream.bit_rate, field_name="bit_rate")
    if bit_rate is None and payload.format is not None:
        bit_rate = _positive_int(payload.format.bit_rate, field_name="bit_rate")

    timeline = VideoTimelineContract(
        fps_num=frame_rate.numerator,
        fps_den=frame_rate.denominator,
        duration_seconds=duration,
        frame_count=frame_count,
        variable_frame_rate=variable_frame_rate,
        time_base_num=time_base.numerator if time_base is not None else None,
        time_base_den=time_base.denominator if time_base is not None else None,
    )
    return VideoArtifactProbe(
        codec_name=stream.codec_name,
        pixel_format=stream.pix_fmt,
        width=stream.width,
        height=stream.height,
        bit_rate_bps=bit_rate,
        size_bytes=size_bytes,
        timeline=timeline,
    )


def probe_video_frame_timestamps(path: Path) -> list[FramePresentationTimestamp]:
    """Return exact timestamp ticks and seconds for every decoded video frame."""
    ffprobe = ffmpeg_wrapper.resolve_ffprobe_executable()
    if ffprobe is None:
        raise VideoStorageNormalizationError("ffprobe executable is not available")
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_frames",
        "-show_entries",
        "frame=best_effort_timestamp,best_effort_timestamp_time",
        "-of",
        "json",
        str(Path(path)),
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=3600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VideoStorageNormalizationError(
            f"Could not probe frame PTS for {path}"
        ) from exc
    try:
        payload = _FrameProbePayload.model_validate(json.loads(completed.stdout))
    except (ValueError, TypeError) as exc:
        raise VideoStorageNormalizationError(
            f"ffprobe returned invalid frame PTS for {path}"
        ) from exc
    timestamps: list[FramePresentationTimestamp] = []
    for index, row in enumerate(payload.frames):
        raw_timestamp = row.best_effort_timestamp_time
        raw_tick = row.best_effort_timestamp
        if raw_timestamp is None or raw_tick is None:
            raise VideoStorageNormalizationError(
                f"Frame {index} has no presentation timestamp"
            )
        try:
            timestamp = float(raw_timestamp)
            tick = int(raw_tick)
        except ValueError as exc:
            raise VideoStorageNormalizationError(
                f"Frame {index} has an invalid presentation timestamp"
            ) from exc
        if not math.isfinite(timestamp) or timestamp < 0 or tick < 0:
            raise VideoStorageNormalizationError(
                f"Frame {index} has an invalid presentation timestamp"
            )
        if timestamps and (
            timestamp <= timestamps[-1].presentation_time_seconds
            or tick <= timestamps[-1].presentation_timestamp
        ):
            raise VideoStorageNormalizationError(
                "Frame presentation timestamps must be strictly increasing"
            )
        timestamps.append(
            FramePresentationTimestamp(
                presentation_timestamp=tick,
                presentation_time_seconds=timestamp,
            )
        )
    if not timestamps:
        raise VideoStorageNormalizationError("ffprobe returned no frame timestamps")
    return timestamps


def probe_video_frame_pts(path: Path) -> list[float]:
    """Compatibility projection of exact frame timestamps to seconds."""
    return [row.presentation_time_seconds for row in probe_video_frame_timestamps(path)]

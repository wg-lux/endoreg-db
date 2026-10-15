"""Bounded admission budgets for exhaustive video re-import."""

from dataclasses import dataclass
from math import ceil

from endoreg_db.config.env import env_float, env_int

MAX_REIMPORT_SOFT_SECONDS = 24 * 60 * 60
REIMPORT_CLEANUP_SECONDS = 300


@dataclass(frozen=True)
class VideoReimportBudget:
    soft_seconds: int
    hard_seconds: int


def video_reimport_budget(frame_count: int | None) -> VideoReimportBudget:
    # A planning estimate, not a benchmark. Operators calibrate this rate using
    # representative whole-pipeline measurements on the selected worker class.
    rate = env_float("VIDEO_REIMPORT_FRAMES_PER_SECOND", 5.0)
    margin = env_float("VIDEO_REIMPORT_TIME_MARGIN", 1.5)
    overhead = env_int("VIDEO_REIMPORT_FINALIZATION_SECONDS", 3600, minimum=1)
    maximum = env_int(
        "VIDEO_REIMPORT_MAX_SECONDS", MAX_REIMPORT_SOFT_SECONDS, minimum=1
    )
    if rate <= 0 or margin < 1 or maximum > MAX_REIMPORT_SOFT_SECONDS:
        raise ValueError("Invalid video re-import budget configuration")
    if frame_count is None or isinstance(frame_count, bool) or frame_count <= 0:
        raise ValueError("video_reimport_frame_count_required")
    # Check against the cap before rounding, including overflow to infinity.
    estimate = frame_count / rate * margin + overhead
    if estimate > maximum:
        raise ValueError("video_reimport_budget_exceeded")
    soft = ceil(estimate)
    return VideoReimportBudget(soft, soft + REIMPORT_CLEANUP_SECONDS)

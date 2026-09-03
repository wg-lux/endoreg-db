# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from django.db import OperationalError
from tqdm import tqdm
from endoreg_db.utils.rust_backend import (
    build_expected_frame_records as rust_build_expected_frame_records,
)
from endoreg_db.utils.rust_backend import (
    build_frame_records as rust_build_frame_records,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.frame.frame import Frame
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _FrameInitializationPlan:
    records: list[tuple[int, str]]
    frame_count: int
    mark_as_extracted: bool
    progress_description: str


def _provided_frame_records(frame_paths: list[Path]) -> list[tuple[int, str]]:
    rust_records = rust_build_frame_records(frame_paths)
    if rust_records is not None:
        return rust_records

    frame_records: list[tuple[int, str]] = []
    for frame_path in frame_paths:
        try:
            frame_records.append((int(frame_path.stem.split("_")[-1]), frame_path.name))
        except (ValueError, IndexError) as error:
            logger.warning(
                "Could not parse frame number from %s: %s",
                frame_path.name,
                error,
            )
    return frame_records


def _expected_frame_records(frame_count: int) -> list[tuple[int, str]]:
    rust_records = rust_build_expected_frame_records(frame_count)
    if rust_records is not None:
        return rust_records
    return [
        (frame_number, f"frame_{frame_number:07d}.jpg")
        for frame_number in range(frame_count)
    ]


def _build_initialization_plan(
    video: "VideoFile",
    frame_paths: list[Path] | None,
) -> _FrameInitializationPlan | None:
    if frame_paths:
        logger.info(
            "Initializing Frame objects based on %d provided paths for video %s.",
            len(frame_paths),
            video.video_hash,
        )
        return _FrameInitializationPlan(
            records=_provided_frame_records(frame_paths),
            frame_count=len(frame_paths),
            mark_as_extracted=True,
            progress_description=f"Initializing Frames from Paths {video.video_hash}",
        )

    expected_frame_count = video.frame_count
    if expected_frame_count is None or expected_frame_count <= 0:
        logger.warning(
            "Cannot initialize frames for video %s: Frame count is %s.",
            video.video_hash,
            expected_frame_count,
        )
        return None
    logger.info(
        "Initializing %d expected Frame objects for video %s (is_extracted=False).",
        expected_frame_count,
        video.video_hash,
    )
    return _FrameInitializationPlan(
        records=_expected_frame_records(expected_frame_count),
        frame_count=expected_frame_count,
        mark_as_extracted=False,
        progress_description=f"Initializing Expected Frames {video.video_hash}",
    )


def _build_frame_objects(
    video: "VideoFile",
    plan: _FrameInitializationPlan,
) -> list["Frame"]:
    from endoreg_db.services.video_files._frames._create_frame_object import (
        _create_frame_object,
    )

    return [
        _create_frame_object(
            video,
            frame_number,
            relative_path,
            extracted=plan.mark_as_extracted,
        )
        for frame_number, relative_path in tqdm(
            plan.records,
            desc=plan.progress_description,
            unit="frame",
        )
    ]


def _reset_empty_initialization_state(video: "VideoFile") -> None:
    try:
        state = video.get_or_create_state()
        if state.frames_initialized or state.frame_count is not None:
            state.frames_initialized = False
            state.frame_count = None
            state.save(update_fields=["frames_initialized", "frame_count"])
    except Exception as error:
        logger.error(
            "Failed to reset state during empty initialization for video %s: %s",
            video.video_hash,
            error,
            exc_info=True,
        )


def _mark_existing_frames_extracted(
    video: "VideoFile",
    frames: list["Frame"],
) -> None:
    from endoreg_db.models.media.frame.frame import Frame

    frame_numbers = [frame.frame_number for frame in frames]
    if not frame_numbers:
        return
    update_count = Frame.objects.filter(
        video=video,
        frame_number__in=frame_numbers,
        is_extracted=False,
    ).update(is_extracted=True)
    if update_count > 0:
        logger.info(
            "Marked %d existing Frame objects as is_extracted=True for video %s.",
            update_count,
            video.video_hash,
        )


def _update_initialization_state(
    video: "VideoFile",
    *,
    frame_count: int,
) -> None:
    try:
        state = video.get_or_create_state()
        state.frames_initialized = True
        state.frame_count = frame_count
        state.save(update_fields=["frames_initialized", "frame_count"])
    except Exception as error:
        logger.error(
            "Failed to update state after frame initialization for video %s: %s",
            video.video_hash,
            error,
            exc_info=True,
        )
        raise RuntimeError(
            "Failed to update state after frame initialization for video "
            f"{video.video_hash}"
        ) from error
    logger.info(
        "Set frames_initialized=True and frame_count=%d for video %s.",
        frame_count,
        video.video_hash,
    )


def _persist_initialization_attempt(
    video: "VideoFile",
    *,
    frames: list["Frame"],
    plan: _FrameInitializationPlan,
) -> None:
    from endoreg_db.services.video_files._frames._bulk_create_frames import (
        _bulk_create_frames,
    )

    _bulk_create_frames(video, frames)
    logger.info(
        "Bulk create attempted for %d Frame objects for video %s "
        "(ignore_conflicts=True).",
        len(frames),
        video.video_hash,
    )
    if plan.mark_as_extracted:
        _mark_existing_frames_extracted(video, frames)
    _update_initialization_state(video, frame_count=plan.frame_count)


def _database_is_locked(error: OperationalError) -> bool:
    return "database is locked" in str(error)


def _persist_initialization_with_retry(
    video: "VideoFile",
    *,
    frames: list["Frame"],
    plan: _FrameInitializationPlan,
) -> None:
    for attempt in range(5):
        try:
            _persist_initialization_attempt(video, frames=frames, plan=plan)
            return
        except OperationalError as error:
            if not _database_is_locked(error):
                logger.error(
                    "Error initializing frames for video %s: %s",
                    video.video_hash,
                    error,
                    exc_info=True,
                )
                raise RuntimeError(
                    f"Failed to initialize frames for video {video.video_hash}."
                ) from error
            logger.warning(
                "Database is locked, retrying frame initialization for video %s "
                "(attempt %d/5).",
                video.video_hash,
                attempt + 1,
            )
            time.sleep(2**attempt)
    raise RuntimeError(f"Failed to initialize frames for video {video.video_hash}.")


def _initialize_frames(
    video: "VideoFile",
    frame_paths: list[Path] | None = None,
) -> None:
    """
    Initializes Frame objects in the database based on either provided existing
    frame paths or the expected frame count from the video metadata.

    If `frame_paths` is provided, Frame objects are created/updated and marked
    as `is_extracted=True`.
    If `frame_paths` is None, Frame objects are created based on `video.frame_count`
    and marked as `is_extracted=False`.

    Updates state.frames_initialized and state.frame_count.
    Uses bulk_create with ignore_conflicts=True, so it won't fail if frames already exist.

    Raises RuntimeError on failure to create/update frames or update state.

    State Transitions:
        - On Success: Sets state.frames_initialized=True, state.frame_count=N.
        - On Failure: Does not change state (error is raised).
    """
    plan = _build_initialization_plan(video, frame_paths)
    if plan is None:
        _reset_empty_initialization_state(video)
        return
    frames = _build_frame_objects(video, plan)
    if not frames:
        logger.warning(
            "No valid frames found/generated to initialize for video %s.",
            video.video_hash,
        )
        _reset_empty_initialization_state(video)
        return
    _persist_initialization_with_retry(video, frames=frames, plan=plan)

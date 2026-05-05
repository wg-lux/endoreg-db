import logging
from pathlib import Path
from typing import TYPE_CHECKING

from django.db import transaction

from endoreg_db.models.media.video.video_file_io import _get_frame_dir_path
from endoreg_db.utils.file_operations import ensure_directory, safe_unlink_file
from endoreg_db.utils.storage import materialize_video_file

# Assuming ffmpeg_wrapper has or will have this function
from endoreg_db.utils.video.ffmpeg_wrapper import (
    extract_frame_range as ffmpeg_extract_frame_range,
)

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile

logger = logging.getLogger(__name__)


def _raw_video_source_context(video: "VideoFile"):
    return materialize_video_file(video, "raw")


def _video_source_context(video: "VideoFile", *, from_processed: bool):
    if from_processed:
        return materialize_video_file(video, "processed")
    return _raw_video_source_context(video)


def _expected_relative_path(frame_number: int, ext: str) -> str:
    return f"frame_{frame_number:07d}.{ext}"


def _ensure_stable_frame_rows(
    video: "VideoFile",
    start_frame: int,
    end_frame: int,
    ext: str,
):
    from endoreg_db.models.media.frame import Frame

    existing_frames = {
        frame.frame_number: frame
        for frame in Frame.objects.filter(
            video=video,
            frame_number__gte=start_frame,
            frame_number__lt=end_frame,
        )
    }

    frames_to_create: list[Frame] = []
    frames_to_update: list[Frame] = []
    for frame_number in range(start_frame, end_frame):
        expected_relative_path = _expected_relative_path(frame_number, ext)
        frame = existing_frames.get(frame_number)
        if frame is None:
            frames_to_create.append(
                Frame(
                    video=video,
                    frame_number=frame_number,
                    relative_path=expected_relative_path,
                    is_extracted=False,
                )
            )
            continue
        if frame.relative_path != expected_relative_path:
            frame.relative_path = expected_relative_path
            frames_to_update.append(frame)

    if frames_to_create:
        Frame.objects.bulk_create(frames_to_create, ignore_conflicts=True)
    if frames_to_update:
        Frame.objects.bulk_update(frames_to_update, ["relative_path"])


def _all_range_files_available(
    video: "VideoFile",
    start_frame: int,
    end_frame: int,
    ext: str,
) -> bool:
    frame_dir = _get_frame_dir_path(video)
    if frame_dir is None:
        return False
    return all(
        (frame_dir / _expected_relative_path(frame_number, ext)).is_file()
        for frame_number in range(start_frame, end_frame)
    )


def extract_frame_range_to_directory(
    video: "VideoFile",
    *,
    output_dir: Path,
    start_frame: int,
    end_frame: int,
    quality: int = 2,
    ext: str = "jpg",
    from_processed: bool = False,
) -> list[Path]:
    ensure_directory(output_dir)
    with _video_source_context(video, from_processed=from_processed) as source_path:
        if not Path(source_path).exists():
            raise FileNotFoundError(
                f"Video file not found at {source_path} for video {video.video_hash}. Cannot extract frame range."
            )

        extracted_paths = ffmpeg_extract_frame_range(
            Path(source_path),
            output_dir,
            start_frame,
            end_frame,
            quality=quality,
            ext=ext,
        )

    missing_files = [
        frame_number
        for frame_number in range(start_frame, end_frame)
        if not (output_dir / _expected_relative_path(frame_number, ext)).is_file()
    ]
    if missing_files:
        raise RuntimeError(
            "Frame range extraction completed but stable files are missing for "
            f"video {video.video_hash}: missing_sample={missing_files[:10]}"
        )

    return extracted_paths


def _delete_frame_range(video: "VideoFile", start_frame: int, end_frame: int):
    """
    Deletes frame image files within the specified range [start_frame, end_frame)
    and updates their is_extracted status to False. Runs within the caller's transaction.
    """

    logger.info(
        "Deleting frame files for video %s in range [%d, %d)",
        video.video_hash,
        start_frame,
        end_frame,
    )
    frames_to_delete = video.frames.filter(
        frame_number__gte=start_frame,
        frame_number__lt=end_frame,
    )

    deleted_count = 0
    paths_to_delete = [
        frame.file_path for frame in frames_to_delete
    ]  # Get paths before potential DB changes

    # Update DB first
    update_count = frames_to_delete.update(is_extracted=False)
    logger.info(
        "Marked %d Frame objects as is_extracted=False for video %s range [%d, %d).",
        update_count,
        video.video_hash,
        start_frame,
        end_frame,
    )

    # Then delete files
    for frame_path in paths_to_delete:
        try:
            if frame_path.exists():
                safe_unlink_file(frame_path)
                deleted_count += 1
        except Exception as e:
            # Log warning but continue; DB state is already updated.
            logger.warning(
                "Could not delete frame file %s for video %s: %s",
                frame_path,
                video.video_hash,
                e,
            )

    logger.info(
        "Attempted deletion of %d files for video %s range [%d, %d). Actual deleted: %d.",
        len(paths_to_delete),
        video.video_hash,
        start_frame,
        end_frame,
        deleted_count,
    )


@transaction.atomic
def _extract_frame_range(
    video: "VideoFile",
    start_frame: int,
    end_frame: int,
    quality: int = 2,
    overwrite: bool = False,
    ext="jpg",
    verbose=False,
) -> bool:
    """
    Extract frames within [start_frame, end_frame) using ffmpeg.

    Range extraction is allowed to satisfy on-demand callers, such as frame
    streaming, but it is not a complete video extraction. It verifies stable files
    before skipping and does not update `VideoState.frames_extracted`.
    """

    if not video.has_raw:
        raise FileNotFoundError(
            f"Raw video file not available for {video.video_hash}. Cannot extract frame range."
        )

    with _raw_video_source_context(video) as source_path:
        if not Path(source_path).exists():
            raise FileNotFoundError(
                f"Raw video file not found at {source_path} for video {video.video_hash}. Cannot extract frame range."
            )

    frame_dir = _get_frame_dir_path(video)
    if not frame_dir:
        raise ValueError(
            f"Cannot determine frame directory path for video {video.video_hash}."
        )

    # Check frames within the range that might already exist
    _ensure_stable_frame_rows(video, start_frame, end_frame, ext)
    frames_in_range = video.frames.filter(
        frame_number__gte=start_frame, frame_number__lt=end_frame
    )
    existing_extracted_in_range = frames_in_range.filter(is_extracted=True)

    if existing_extracted_in_range.exists():
        range_files_available = _all_range_files_available(
            video,
            start_frame,
            end_frame,
            ext,
        )
        if overwrite:
            logger.info(
                "Overwrite=True, deleting existing frame files in range [%d, %d) for video %s before extraction.",
                start_frame,
                end_frame,
                video.video_hash,
            )
            _delete_frame_range(video, start_frame, end_frame)
        elif range_files_available:
            logger.info(
                "Frames already exist in range [%d, %d) for video %s and overwrite=False. Skipping extraction for this range.",
                start_frame,
                end_frame,
                video.video_hash,
            )
            updated_count = frames_in_range.filter(is_extracted=False).update(
                is_extracted=True
            )
            if updated_count > 0:
                logger.info(
                    "Marked %d existing Frame objects in range [%d, %d) as extracted for video %s.",
                    updated_count,
                    start_frame,
                    end_frame,
                    video.video_hash,
                )
            return True  # Indicate success as frames are considered present
        else:
            logger.warning(
                "Frame DB flags indicated extracted frames in range [%d, %d) for video %s, but stable files are missing. Re-extracting range.",
                start_frame,
                end_frame,
                video.video_hash,
            )
            _delete_frame_range(video, start_frame, end_frame)

    ensure_directory(frame_dir)
    extracted_paths = []

    try:
        logger.info(
            "Starting frame range extraction [%d, %d) for video %s to %s",
            start_frame,
            end_frame,
            video.video_hash,
            frame_dir,
        )
        extracted_paths = extract_frame_range_to_directory(
            video,
            output_dir=frame_dir,
            start_frame=start_frame,
            end_frame=end_frame,
            quality=quality,
            ext=ext,
        )

        logger.info(
            "ffmpeg extraction process completed for video %s range [%d, %d). Found %d files.",
            video.video_hash,
            start_frame,
            end_frame,
            len(extracted_paths),
        )

        _ensure_stable_frame_rows(video, start_frame, end_frame, ext)
        frames_in_range = video.frames.filter(
            frame_number__gte=start_frame,
            frame_number__lt=end_frame,
        )
        update_count = frames_in_range.update(is_extracted=True)
        logger.info(
            "Marked %d Frame objects in range [%d, %d) as is_extracted=True for video %s.",
            update_count,
            start_frame,
            end_frame,
            video.video_hash,
        )

        return True

    except FileNotFoundError as err:
        logger.error(
            "Frame range extraction [%d, %d) failed for video %s: %s",
            start_frame,
            end_frame,
            video.video_hash,
            err,
            exc_info=True,
        )
        raise

    except Exception as e:
        logger.error(
            "Frame range extraction [%d, %d) or DB update failed for video %s: %s",
            start_frame,
            end_frame,
            video.video_hash,
            e,
            exc_info=True,
        )

        logger.warning(
            "Attempting file cleanup in range [%d, %d) for video %s due to extraction error.",
            start_frame,
            end_frame,
            video.video_hash,
        )
        files_to_check = (
            extracted_paths if "extracted_paths" in locals() and extracted_paths else []
        )
        if not files_to_check:
            files_to_check = [
                frame_dir / f"frame_{i:07d}.{ext}"
                for i in range(start_frame, end_frame)
            ]

        for potential_file in files_to_check:
            if potential_file.exists():
                try:
                    safe_unlink_file(potential_file)
                except OSError as unlink_err:
                    logger.error(
                        "Failed to delete potential frame %s during cleanup: %s",
                        potential_file,
                        unlink_err,
                    )

        raise RuntimeError(
            f"Frame range extraction or update failed for video {video.video_hash} range [{start_frame}, {end_frame})."
        ) from e

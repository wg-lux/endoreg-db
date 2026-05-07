import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from endoreg_db.models.media.video.video_file_io import _get_frame_dir_path
from endoreg_db.utils.storage import materialize_video_file
from endoreg_db.utils.file_operations import (
    atomic_move_file,
    atomic_move_path,
    ensure_directory,
    safe_rmtree,
    safe_unlink_file,
)
from endoreg_db.utils.video.ffmpeg_wrapper import (
    extract_frames as ffmpeg_extract_frames,
)

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile

from django.db import transaction

from endoreg_db.utils.rust_backend import parse_extracted_frame_numbers as rust_parse

logger = logging.getLogger(__name__)


def _video_source_context(video: "VideoFile", *, from_processed: bool):
    return materialize_video_file(
        video,
        "processed" if from_processed else "raw",
    )


def _expected_relative_path(frame_number: int, ext: str) -> str:
    return f"frame_{frame_number:07d}.{ext}"


def _expected_frame_count(video: "VideoFile", state) -> int | None:
    for value in (
        getattr(video, "frame_count", None),
        getattr(state, "frame_count", None),
    ):
        if value is None:
            continue
        try:
            count = int(str(value))
        except (TypeError, ValueError):
            continue
        if count > 0:
            return count
    return None


def _parse_frame_numbers(frame_paths: list[Path]) -> list[int]:
    rust_frame_numbers = rust_parse(frame_paths)
    if rust_frame_numbers is not None:
        return rust_frame_numbers

    frame_numbers: list[int] = []
    for frame_path in frame_paths:
        try:
            frame_numbers.append(int(frame_path.stem.split("_")[-1]))
        except (ValueError, IndexError) as e:
            logger.warning(
                "Could not parse frame number from extracted file %s: %s",
                frame_path.name,
                e,
            )
    return frame_numbers


def _full_extraction_files_complete(
    frame_dir: Path,
    *,
    expected_count: int,
    ext: str,
) -> bool:
    """Return true only when the directory is an exact full extraction."""
    expected_names = {
        _expected_relative_path(frame_number, ext)
        for frame_number in range(expected_count)
    }
    actual_names = {
        frame_path.name
        for frame_path in frame_dir.glob(f"frame_*.{ext}")
        if frame_path.is_file()
    }
    return actual_names == expected_names


def _get_staged_extraction_dir(frame_dir: Path, video_hash: str) -> Path:
    return frame_dir.with_name(f".extracting_{video_hash}_{os.getpid()}_{uuid4().hex}")


def _get_staged_replacement_dir(frame_dir: Path) -> Path:
    return frame_dir.with_name(f"{frame_dir.name}.pending_replace.{uuid4().hex}")


def _ensure_stable_frame_records(
    video: "VideoFile",
    *,
    frame_numbers: list[int],
    ext: str,
) -> int:
    from endoreg_db.models.media.frame import Frame

    if not frame_numbers:
        return 0

    unique_numbers = sorted(set(frame_numbers))
    existing_frames = {
        frame.frame_number: frame
        for frame in Frame.objects.filter(
            video=video,
            frame_number__in=unique_numbers,
        )
    }

    frames_to_create: list[Frame] = []
    frames_to_update: list[Frame] = []
    for frame_number in unique_numbers:
        expected_relative_path = _expected_relative_path(frame_number, ext)
        frame = existing_frames.get(frame_number)
        if frame is None:
            frames_to_create.append(
                Frame(
                    video=video,
                    frame_number=frame_number,
                    relative_path=expected_relative_path,
                    is_extracted=True,
                )
            )
            continue

        changed = False
        if frame.relative_path != expected_relative_path:
            frame.relative_path = expected_relative_path
            changed = True
        if not frame.is_extracted:
            frame.is_extracted = True
            changed = True
        if changed:
            frames_to_update.append(frame)

    if frames_to_create:
        Frame.objects.bulk_create(frames_to_create, ignore_conflicts=True)
    if frames_to_update:
        Frame.objects.bulk_update(
            frames_to_update,
            ["relative_path", "is_extracted"],
        )

    return len(unique_numbers)


def extract_full_frame_set_to_directory(
    video: "VideoFile",
    *,
    output_dir: Path,
    quality: int = 2,
    ext: str = "jpg",
    from_processed: bool = False,
) -> list[Path]:
    if from_processed:
        source_label = "Processed"
    else:
        if not video.has_raw:
            raise FileNotFoundError(
                f"Raw video file not available for {video.video_hash}. Cannot extract frames."
            )
        source_label = "Raw"

    with _video_source_context(video, from_processed=from_processed) as source_path:
        if not Path(source_path).exists():
            raise FileNotFoundError(
                f"{source_label} video file not found at {source_path} for video {video.video_hash}. Cannot extract frames."
            )
        ensure_directory(output_dir)
        return ffmpeg_extract_frames(
            Path(source_path),
            output_dir,
            quality=quality,
            ext=ext,
        )


def _normalize_full_extraction_paths(
    frame_paths: list[Path],
    *,
    frame_dir: Path,
    ext: str,
) -> list[Path]:
    """
    Normalize full-extraction output to stable zero-based DB paths.

    FFmpeg is invoked with ``-start_number 0`` now, but this also repairs output
    from older/mocked extractors that still emit one-based or short-padded names.
    """
    if not frame_paths:
        return []

    parsed: list[tuple[int, Path]] = []
    for frame_path in frame_paths:
        try:
            parsed.append((int(frame_path.stem.split("_")[-1]), frame_path))
        except (ValueError, IndexError) as exc:
            raise RuntimeError(
                f"Could not parse extracted frame filename: {frame_path.name}"
            ) from exc

    sorted_paths = [path for _, path in sorted(parsed, key=lambda item: item[0])]
    target_paths = [
        frame_dir / _expected_relative_path(frame_number, ext)
        for frame_number in range(len(sorted_paths))
    ]

    if all(source == target for source, target in zip(sorted_paths, target_paths)):
        return target_paths

    staged_paths: list[Path] = []
    rename_token = f".renaming.{id(sorted_paths)}"
    try:
        for index, source_path in enumerate(sorted_paths):
            staged_path = frame_dir / f"{source_path.name}{rename_token}.{index}"
            atomic_move_file(source=source_path, destination=staged_path)
            staged_paths.append(staged_path)

        for staged_path, target_path in zip(staged_paths, target_paths):
            if target_path.exists():
                safe_unlink_file(target_path, missing_ok=True)
            atomic_move_file(source=staged_path, destination=target_path)
    except Exception:
        for staged_path in staged_paths:
            if staged_path.exists():
                safe_unlink_file(staged_path, missing_ok=True)
        raise

    return target_paths


def _extract_frames(
    video: "VideoFile",
    quality: int = 2,
    overwrite: bool = False,
    ext="jpg",
    verbose=False,
    from_processed: bool = False,
) -> bool:
    """
    Extract a complete, stable frame set and update frame extraction state.

    Full extraction is skipped only when the frame directory exactly matches the
    expected zero-based filename set for the known frame count. A non-empty
    frame directory, stale state flag, or range-extracted single frame is treated
    as incomplete and replaced only after a staged extraction verifies the full
    expected frame set. This protects `pipe_1` from running OCR/prediction on a
    partial frame set.

    Parameters:
        video (VideoFile): The video object from which frames are to be extracted.
        quality (int, optional): Quality parameter for ffmpeg extraction. Defaults to 2.
        overwrite (bool, optional): Whether to overwrite existing extracted frames. Defaults to False.
        ext (str, optional): File extension for extracted frames. Defaults to "jpg".

    Returns:
        bool: True if extraction and updates succeed.

    Raises:
        FileNotFoundError: If the raw video file is missing.
        RuntimeError: If extraction or database update fails.
        ValueError: If the frame directory path cannot be determined.
    """
    from endoreg_db.models.media.frame import Frame

    frame_dir = _get_frame_dir_path(video)
    if not frame_dir:
        raise ValueError(
            f"Cannot determine frame directory path for video {video.video_hash}."
        )

    state = video.get_or_create_state()
    expected_count = _expected_frame_count(video, state)
    files_exist_on_disk = frame_dir.exists() and any(frame_dir.glob(f"frame_*.{ext}"))
    existing_full_extraction_complete = False
    if expected_count is not None and frame_dir.exists():
        existing_full_extraction_complete = _full_extraction_files_complete(
            frame_dir,
            expected_count=expected_count,
            ext=ext,
        )

    # Fast-path: only reuse existing full extraction if every expected file is
    # present; stable DB rows are verified or repaired before returning.
    if existing_full_extraction_complete and not overwrite:
        logger.info(
            "Complete frame extraction already exists for video %s (%d frames), and overwrite=False. Skipping extraction.",
            video.video_hash,
            expected_count,
        )
        with transaction.atomic():
            state.refresh_from_db()
            assert expected_count is not None
            frame_numbers = list(range(expected_count))
            updated_count = _ensure_stable_frame_records(
                video,
                frame_numbers=frame_numbers,
                ext=ext,
            )
            logger.info(
                "Verified %d stable Frame records for video %s based on complete files.",
                updated_count,
                video.video_hash,
            )
            if not state.frames_extracted:
                state.mark_frames_extracted(save=True)
        return True

    if (state.frames_extracted or files_exist_on_disk) and not overwrite:
        logger.warning(
            "Frame extraction state/files for video %s are incomplete. A staged full extraction will replace the cache after verification.",
            video.video_hash,
        )

    if overwrite:
        logger.info(
            "Overwrite=True. A staged full extraction will replace existing frames/files for video %s after verification.",
            video.video_hash,
        )

    ensure_directory(frame_dir.parent)
    staged_frame_dir = _get_staged_extraction_dir(frame_dir, str(video.video_hash))
    replaced_frame_dir: Path | None = None
    installed_new_cache = False
    corrected_frame_count: int | None = None

    try:
        logger.info(
            "Starting staged frame extraction for video %s to %s",
            video.video_hash,
            staged_frame_dir,
        )
        # Step 1: Perform the long-running frame extraction outside any transaction.
        extracted_paths = extract_full_frame_set_to_directory(
            video,
            output_dir=staged_frame_dir,
            quality=quality,
            ext=ext,
            from_processed=from_processed,
        )
        if not extracted_paths:
            logger.warning(
                "ffmpeg_extract_frames returned no paths for video %s. Check video duration and ffmpeg logs.",
                video.video_hash,
            )
            if video.frame_count is not None and video.frame_count > 0:
                raise RuntimeError(
                    f"ffmpeg_extract_frames returned no paths for video {video.video_hash}, but {video.frame_count} frames were expected."
                )

        extracted_paths = _normalize_full_extraction_paths(
            extracted_paths,
            frame_dir=staged_frame_dir,
            ext=ext,
        )

        logger.info(
            "Successfully extracted %d frames using ffmpeg for video %s.",
            len(extracted_paths),
            video.video_hash,
        )

        extracted_frame_numbers = _parse_frame_numbers(extracted_paths)
        if expected_count is not None:
            expected_frame_numbers = set(range(expected_count))
            extracted_frame_number_set = set(extracted_frame_numbers)
            if extracted_frame_number_set != expected_frame_numbers:
                missing = sorted(expected_frame_numbers - extracted_frame_number_set)
                extra = sorted(extracted_frame_number_set - expected_frame_numbers)
                has_single_trailing_extra = (
                    not missing
                    and len(extra) == 1
                    and extra[0] == expected_count
                    and len(extracted_frame_number_set) == expected_count + 1
                )
                if has_single_trailing_extra:
                    previous_expected_count = expected_count
                    corrected_frame_count = len(extracted_frame_number_set)
                    expected_count = corrected_frame_count
                    logger.warning(
                        "Correcting decoded frame count for video %s from %d to %d "
                        "after FFmpeg extracted one trailing frame beyond metadata.",
                        video.video_hash,
                        previous_expected_count,
                        corrected_frame_count,
                    )
                else:
                    raise RuntimeError(
                        "Extracted frame set does not match expected video frame count "
                        f"for {video.video_hash}: expected={expected_count}, "
                        f"actual={len(extracted_frame_number_set)}, "
                        f"missing_sample={missing[:10]}, extra_sample={extra[:10]}"
                    )

        if frame_dir.exists():
            replaced_frame_dir = _get_staged_replacement_dir(frame_dir)
            atomic_move_path(source=frame_dir, destination=replaced_frame_dir)
        atomic_move_path(source=staged_frame_dir, destination=frame_dir)
        installed_new_cache = True
        extracted_paths = [frame_dir / path.name for path in extracted_paths]

        # Step 2: Perform all the quick DB updates inside a minimal atomic transaction.
        with transaction.atomic():
            Frame.objects.filter(video=video, is_extracted=True).update(
                is_extracted=False
            )
            if extracted_frame_numbers:
                try:
                    update_count = _ensure_stable_frame_records(
                        video,
                        frame_numbers=extracted_frame_numbers,
                        ext=ext,
                    )
                    logger.info(
                        "Ensured %d stable Frame objects as is_extracted=True for video %s.",
                        update_count,
                        video.video_hash,
                    )
                    if update_count != len(extracted_frame_numbers):
                        logger.warning(
                            "Number of updated frames (%d) does not match number of parsed extracted files (%d) for video %s.",
                            update_count,
                            len(extracted_frame_numbers),
                            video.video_hash,
                        )
                except Exception as update_e:
                    logger.error(
                        "Failed to update is_extracted flag for frames of video %s: %s",
                        video.video_hash,
                        update_e,
                        exc_info=True,
                    )
                    raise
            state.refresh_from_db()
            if (
                corrected_frame_count is not None
                and video.frame_count != corrected_frame_count
            ):
                video.frame_count = corrected_frame_count
                video.save(update_fields=["frame_count"])
            if not state.frames_initialized:
                state.frames_initialized = True
            if state.frame_count != len(extracted_frame_numbers):
                state.frame_count = len(extracted_frame_numbers)
            state.mark_frames_extracted(save=False)
            state.save(
                update_fields=[
                    "frames_initialized",
                    "frame_count",
                    "frames_extracted",
                    "date_modified",
                ]
            )
        if replaced_frame_dir is not None:
            safe_rmtree(replaced_frame_dir, missing_ok=True)
        return True

    except Exception as e:
        logger.error(
            "Frame extraction or update failed for video %s: %s",
            video.video_hash,
            e,
            exc_info=True,
        )
        logger.warning(
            "Cleaning up staged frame directory %s for video %s due to extraction error.",
            staged_frame_dir,
            video.video_hash,
        )
        safe_rmtree(staged_frame_dir, missing_ok=True)
        if replaced_frame_dir is not None and replaced_frame_dir.exists():
            if frame_dir.exists():
                safe_rmtree(frame_dir, missing_ok=True)
            try:
                atomic_move_path(source=replaced_frame_dir, destination=frame_dir)
            except Exception as restore_err:
                logger.error(
                    "Failed to restore previous frame cache for video %s from %s: %s",
                    video.video_hash,
                    replaced_frame_dir,
                    restore_err,
                    exc_info=True,
                )
        elif installed_new_cache and frame_dir.exists():
            safe_rmtree(frame_dir, missing_ok=True)
        try:
            with transaction.atomic():
                state.refresh_from_db()
                if state.frames_extracted:
                    state.frames_extracted = False
                    state.save(update_fields=["frames_extracted"])
        except Exception as db_err:
            logger.error(
                "Failed to reset flags/state in DB during error handling for video %s: %s",
                video.video_hash,
                db_err,
            )
        raise RuntimeError(
            f"Frame extraction or update failed for video {video.video_hash}."
        ) from e

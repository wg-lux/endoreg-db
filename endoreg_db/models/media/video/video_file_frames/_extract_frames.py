import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from endoreg_db.models.media.video.video_file_io import _get_frame_dir_path
from endoreg_db.utils.storage import materialize_video_file
from endoreg_db.utils.filesystem.file_operations import (
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

from endoreg_db.utils.system.rust_backend import (
    parse_extracted_frame_numbers as rust_parse,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FrameCacheManifest:
    frame_dir: Path
    ext: str
    expected_count: int | None
    actual_names: list[str]
    frame_numbers: list[int]
    invalid_file_names: list[str]
    duplicate_frame_numbers: list[int]
    missing_frame_numbers: list[int]
    extra_frame_numbers: list[int]
    unexpected_file_names: list[str]

    @property
    def file_count(self) -> int:
        return len(self.actual_names)

    @property
    def is_contiguous_zero_based(self) -> bool:
        return (
            not self.invalid_file_names
            and not self.duplicate_frame_numbers
            and self.frame_numbers == list(range(self.file_count))
        )

    @property
    def is_exact_complete(self) -> bool:
        if self.expected_count is None:
            return False
        expected_names = {
            _expected_relative_path(frame_number, self.ext)
            for frame_number in range(self.expected_count)
        }
        return (
            not self.invalid_file_names
            and not self.duplicate_frame_numbers
            and set(self.actual_names) == expected_names
        )

    def as_log_payload(self) -> dict[str, Any]:
        return {
            "frame_dir": str(self.frame_dir),
            "expected_count": self.expected_count,
            "file_count": self.file_count,
            "missing_frame_numbers": self.missing_frame_numbers[:50],
            "extra_frame_numbers": self.extra_frame_numbers[:50],
            "invalid_file_names": self.invalid_file_names[:50],
            "duplicate_frame_numbers": self.duplicate_frame_numbers[:50],
            "unexpected_file_names": self.unexpected_file_names[:50],
        }


@dataclass(frozen=True, slots=True)
class FrameCacheValidation:
    manifest: FrameCacheManifest
    db_extracted_frame_count: int
    db_missing_frame_numbers: list[int]
    db_extra_frame_numbers: list[int]
    db_path_mismatch_frame_numbers: list[int]
    db_missing_file_frame_numbers: list[int]

    @property
    def valid(self) -> bool:
        return (
            self.manifest.is_exact_complete
            and not self.db_missing_frame_numbers
            and not self.db_extra_frame_numbers
            and not self.db_path_mismatch_frame_numbers
            and not self.db_missing_file_frame_numbers
        )

    def as_log_payload(self) -> dict[str, Any]:
        payload = self.manifest.as_log_payload()
        payload.update(
            {
                "db_extracted_frame_count": self.db_extracted_frame_count,
                "db_missing_frame_numbers": self.db_missing_frame_numbers[:50],
                "db_extra_frame_numbers": self.db_extra_frame_numbers[:50],
                "db_path_mismatch_frame_numbers": (
                    self.db_path_mismatch_frame_numbers[:50]
                ),
                "db_missing_file_frame_numbers": (
                    self.db_missing_file_frame_numbers[:50]
                ),
                "valid": self.valid,
            }
        )
        return payload


def _video_source_context(video: "VideoFile", *, from_processed: bool):
    return materialize_video_file(
        video,
        "processed" if from_processed else "raw",
    )


def _expected_relative_path(frame_number: int, ext: str) -> str:
    return f"frame_{frame_number:07d}.{ext}"


def _log_frame_cache_event(
    *,
    event: str,
    video: "VideoFile",
    status: str,
    detail: str,
    **extra: Any,
) -> None:
    payload = {
        "event": event,
        "video_hash": str(video.video_hash),
        "status": status,
        "detail": detail,
    }
    payload.update(extra)
    logger.warning(json.dumps(payload, sort_keys=True, default=str))


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


def _parse_frame_number_from_name(file_name: str, ext: str) -> int | None:
    prefix = "frame_"
    suffix = f".{ext}"
    if not file_name.startswith(prefix) or not file_name.endswith(suffix):
        return None
    number_text = file_name[len(prefix) : -len(suffix)]
    if not number_text.isdigit():
        return None
    return int(number_text)


def build_frame_cache_manifest(
    frame_dir: Path,
    *,
    expected_count: int | None,
    ext: str,
) -> FrameCacheManifest:
    frame_paths: list[Path] = []
    if frame_dir.exists():
        frame_paths = sorted(
            path for path in frame_dir.glob(f"frame_*.{ext}") if path.is_file()
        )

    actual_names = [path.name for path in frame_paths]
    invalid_file_names: list[str] = []
    frame_numbers_by_name: dict[str, int] = {}
    seen_numbers: set[int] = set()
    duplicate_numbers: set[int] = set()

    for file_name in actual_names:
        frame_number = _parse_frame_number_from_name(file_name, ext)
        if frame_number is None:
            invalid_file_names.append(file_name)
            continue
        frame_numbers_by_name[file_name] = frame_number
        if frame_number in seen_numbers:
            duplicate_numbers.add(frame_number)
        seen_numbers.add(frame_number)

    frame_numbers = sorted(seen_numbers)
    missing_frame_numbers: list[int] = []
    extra_frame_numbers: list[int] = []
    unexpected_file_names: list[str] = []
    if expected_count is not None:
        expected_numbers = set(range(expected_count))
        missing_frame_numbers = sorted(expected_numbers - seen_numbers)
        extra_frame_numbers = sorted(seen_numbers - expected_numbers)
        expected_names = {
            _expected_relative_path(frame_number, ext)
            for frame_number in expected_numbers
        }
        unexpected_file_names = sorted(set(actual_names) - expected_names)
    else:
        unexpected_file_names = sorted(
            file_name
            for file_name, frame_number in frame_numbers_by_name.items()
            if file_name != _expected_relative_path(frame_number, ext)
        )

    return FrameCacheManifest(
        frame_dir=frame_dir,
        ext=ext,
        expected_count=expected_count,
        actual_names=actual_names,
        frame_numbers=frame_numbers,
        invalid_file_names=sorted(invalid_file_names),
        duplicate_frame_numbers=sorted(duplicate_numbers),
        missing_frame_numbers=missing_frame_numbers,
        extra_frame_numbers=extra_frame_numbers,
        unexpected_file_names=unexpected_file_names,
    )


def _resolve_verified_frame_count(
    manifest: FrameCacheManifest,
    *,
    video: "VideoFile",
    expected_count: int | None,
) -> tuple[int, int | None]:
    if manifest.file_count == 0:
        raise RuntimeError(
            f"Frame extraction produced no installed frame files for {video.video_hash}."
        )
    if manifest.invalid_file_names or manifest.duplicate_frame_numbers:
        _log_frame_cache_event(
            event="frame_cache_validation",
            video=video,
            status="invalid",
            detail="staged frame cache contains invalid or duplicate frame names",
            **manifest.as_log_payload(),
        )
        raise RuntimeError(
            "Extracted frame set contains invalid or duplicate frame filenames "
            f"for {video.video_hash}."
        )

    if expected_count is None:
        if manifest.is_contiguous_zero_based:
            return manifest.file_count, None
        _log_frame_cache_event(
            event="frame_cache_validation",
            video=video,
            status="invalid",
            detail="staged frame cache is not contiguous zero-based",
            **manifest.as_log_payload(),
        )
        raise RuntimeError(
            f"Extracted frame set for {video.video_hash} is not contiguous zero-based."
        )

    expected_numbers = set(range(expected_count))
    actual_numbers = set(manifest.frame_numbers)
    if (
        actual_numbers == expected_numbers
        and manifest.file_count == expected_count
        and not manifest.unexpected_file_names
    ):
        return expected_count, None

    missing = sorted(expected_numbers - actual_numbers)
    extra = sorted(actual_numbers - expected_numbers)
    has_single_trailing_extra = (
        not missing
        and extra == [expected_count]
        and actual_numbers == set(range(expected_count + 1))
        and manifest.file_count == expected_count + 1
    )
    if has_single_trailing_extra:
        corrected_count = expected_count + 1
        logger.warning(
            "Correcting decoded frame count for video %s from %d to %d "
            "after FFmpeg extracted one trailing frame beyond metadata.",
            video.video_hash,
            expected_count,
            corrected_count,
        )
        return corrected_count, corrected_count

    _log_frame_cache_event(
        event="frame_cache_validation",
        video=video,
        status="invalid",
        detail="staged frame cache does not match expected frame count",
        **manifest.as_log_payload(),
    )
    raise RuntimeError(
        "Extracted frame set does not match expected video frame count "
        f"for {video.video_hash}: expected={expected_count}, "
        f"actual={manifest.file_count}, missing_sample={missing[:10]}, "
        f"extra_sample={extra[:10]}"
    )


def _assert_exact_installed_manifest(
    manifest: FrameCacheManifest,
    *,
    video: "VideoFile",
) -> None:
    if manifest.is_exact_complete:
        return
    _log_frame_cache_event(
        event="frame_cache_validation",
        video=video,
        status="invalid",
        detail="installed frame cache failed final exact completeness check",
        **manifest.as_log_payload(),
    )
    raise RuntimeError(
        "Installed frame cache does not match the verified frame set "
        f"for {video.video_hash}."
    )


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


def validate_video_frame_cache(
    video: "VideoFile",
    *,
    ext: str = "jpg",
) -> FrameCacheValidation:
    from endoreg_db.models.media.frame import Frame

    state = video.get_or_create_state()
    expected_count = _expected_frame_count(video, state)
    frame_dir = _get_frame_dir_path(video)
    if frame_dir is None:
        raise ValueError(
            f"Cannot determine frame directory path for video {video.video_hash}."
        )

    manifest = build_frame_cache_manifest(
        frame_dir,
        expected_count=expected_count,
        ext=ext,
    )
    expected_paths: dict[int, str] = {}
    if expected_count is not None:
        expected_paths = {
            frame_number: _expected_relative_path(frame_number, ext)
            for frame_number in range(expected_count)
        }

    db_rows = list(
        Frame.objects.filter(video=video, is_extracted=True).values(
            "frame_number",
            "relative_path",
        )
    )
    db_paths = {int(row["frame_number"]): str(row["relative_path"]) for row in db_rows}
    expected_numbers = set(expected_paths)
    db_numbers = set(db_paths)
    db_missing = sorted(expected_numbers - db_numbers)
    db_extra = sorted(db_numbers - expected_numbers)

    db_path_mismatch: list[int] = []
    db_missing_files: list[int] = []
    for frame_number in sorted(expected_numbers & db_numbers):
        relative_path = db_paths[frame_number]
        if relative_path != expected_paths[frame_number]:
            db_path_mismatch.append(frame_number)
        if not (frame_dir / relative_path).is_file():
            db_missing_files.append(frame_number)

    return FrameCacheValidation(
        manifest=manifest,
        db_extracted_frame_count=len(db_rows),
        db_missing_frame_numbers=db_missing,
        db_extra_frame_numbers=db_extra,
        db_path_mismatch_frame_numbers=db_path_mismatch,
        db_missing_file_frame_numbers=db_missing_files,
    )


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


def _sync_extracted_frame_records(
    video: "VideoFile",
    *,
    frame_numbers: list[int],
    ext: str,
) -> int:
    from endoreg_db.models.media.frame import Frame

    unique_numbers = sorted(set(frame_numbers))
    if unique_numbers:
        Frame.objects.filter(video=video, is_extracted=True).exclude(
            frame_number__in=unique_numbers
        ).update(is_extracted=False)
    else:
        Frame.objects.filter(video=video, is_extracted=True).update(is_extracted=False)
        return 0
    return _ensure_stable_frame_records(
        video,
        frame_numbers=unique_numbers,
        ext=ext,
    )


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
        if not frame_path.is_file():
            raise RuntimeError(f"Extractor returned missing frame file: {frame_path}")
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
    frame_dir = _get_frame_dir_path(video)
    if not frame_dir:
        raise ValueError(
            f"Cannot determine frame directory path for video {video.video_hash}."
        )

    state = video.get_or_create_state()
    expected_count = _expected_frame_count(video, state)
    files_exist_on_disk = frame_dir.exists() and any(frame_dir.glob(f"frame_*.{ext}"))
    existing_manifest: FrameCacheManifest | None = None
    existing_full_extraction_complete = False
    if expected_count is not None and frame_dir.exists():
        existing_manifest = build_frame_cache_manifest(
            frame_dir,
            expected_count=expected_count,
            ext=ext,
        )
        existing_full_extraction_complete = existing_manifest.is_exact_complete

    # Fast-path: only reuse existing full extraction if every expected file is
    # present; stable DB rows are verified or repaired before returning.
    if existing_full_extraction_complete and not overwrite:
        assert existing_manifest is not None
        logger.info(
            "Complete frame extraction already exists for video %s (%d frames), and overwrite=False. Skipping extraction.",
            video.video_hash,
            expected_count,
        )
        with transaction.atomic():
            state.refresh_from_db()
            assert expected_count is not None
            frame_numbers = existing_manifest.frame_numbers
            updated_count = _sync_extracted_frame_records(
                video,
                frame_numbers=frame_numbers,
                ext=ext,
            )
            logger.info(
                "Verified %d stable Frame records for video %s based on complete files.",
                updated_count,
                video.video_hash,
            )
            if not state.frames_initialized:
                state.frames_initialized = True
            if state.frame_count != expected_count:
                state.frame_count = expected_count
            state.mark_frames_extracted(save=False)
            state.save(
                update_fields=[
                    "frames_initialized",
                    "frame_count",
                    "frames_extracted",
                    "date_modified",
                ]
            )
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

        staged_manifest = build_frame_cache_manifest(
            staged_frame_dir,
            expected_count=expected_count,
            ext=ext,
        )
        verified_frame_count, corrected_frame_count = _resolve_verified_frame_count(
            staged_manifest,
            video=video,
            expected_count=expected_count,
        )
        expected_count = verified_frame_count
        staged_manifest = build_frame_cache_manifest(
            staged_frame_dir,
            expected_count=expected_count,
            ext=ext,
        )
        _assert_exact_installed_manifest(staged_manifest, video=video)

        if frame_dir.exists():
            replaced_frame_dir = _get_staged_replacement_dir(frame_dir)
            atomic_move_path(source=frame_dir, destination=replaced_frame_dir)
        atomic_move_path(source=staged_frame_dir, destination=frame_dir)
        installed_new_cache = True
        final_manifest = build_frame_cache_manifest(
            frame_dir,
            expected_count=expected_count,
            ext=ext,
        )
        _assert_exact_installed_manifest(final_manifest, video=video)

        # Step 2: Perform all the quick DB updates inside a minimal atomic transaction.
        with transaction.atomic():
            if final_manifest.frame_numbers:
                try:
                    update_count = _sync_extracted_frame_records(
                        video,
                        frame_numbers=final_manifest.frame_numbers,
                        ext=ext,
                    )
                    logger.info(
                        "Ensured %d stable Frame objects as is_extracted=True for video %s.",
                        update_count,
                        video.video_hash,
                    )
                    if update_count != len(final_manifest.frame_numbers):
                        logger.warning(
                            "Number of updated frames (%d) does not match number of parsed extracted files (%d) for video %s.",
                            update_count,
                            len(final_manifest.frame_numbers),
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
            if state.frame_count != len(final_manifest.frame_numbers):
                state.frame_count = len(final_manifest.frame_numbers)
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

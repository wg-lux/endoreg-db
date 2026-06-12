# pyright: reportUnusedFunction=false, reportPrivateUsage=false, reportMissingTypeStubs=false

import json
import logging
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Protocol, cast
from lx_dtypes.models.contracts.endoscopy_processor import (
    RoiBoxCore,
    all_black_fallback_roi_box,
    roi_box_or_none_from_object,
)
from django.db import transaction
from tqdm import tqdm

from endoreg_db.import_files.file_storage.cleanup import safe_cleanup_staging_file
from endoreg_db.services.streamable_media import sync_video_streamable_artifacts
from endoreg_db.utils.hashs import get_video_hash
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.file_operations import (
    ensure_directory,
    safe_rmtree,
    safe_unlink_file,
)
from endoreg_db.utils.storage import save_local_file
from endoreg_db.utils.validate_endo_roi import validate_endo_roi

from endoreg_db.utils.ffmpeg_wrapper import (
    assemble_video_from_frames,
    mask_video_to_roi_and_blacken_intervals,
)
from endoreg_db.models.utils import anonymize_frame  # Import from models.utils
from endoreg_db.services.video_files.frames import extract_video_frames
from endoreg_db.services.video_files._frames._extract_frames import (
    validate_video_frame_cache,
)
from endoreg_db.services.video_files._segments import (
    _get_outside_frame_numbers,
    _get_outside_frames,
    _get_outside_segments,
)

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from endoreg_db.models.media.frame import Frame
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)


class _LocalRawFileProvider(Protocol):
    def ensure_local_raw_file(self) -> AbstractContextManager[Path]: ...


def _video_integrity_failure_detail(video: "VideoFile") -> str:
    payload = video.meta if isinstance(video.meta, dict) else {}
    detail = str(payload.get("integrity_error") or "").strip()
    if detail:
        return detail
    if bool(getattr(getattr(video, "state", None), "processing_error", False)):
        return "video state is marked failed/lost"
    return ""


def _video_has_integrity_failure(video: "VideoFile") -> bool:
    payload = video.meta if isinstance(video.meta, dict) else {}
    return payload.get("integrity_status") == "lost" or bool(
        getattr(getattr(video, "state", None), "processing_error", False)
    )


def _record_frame_cache_mismatch(video: "VideoFile", detail: str) -> None:
    state = video.get_or_create_state()
    if state.frames_extracted:
        state.mark_frames_not_extracted(save=True)
    try:
        from endoreg_db.services.media_integrity import mark_video_integrity_lost

        mark_video_integrity_lost(video, detail)
    except Exception as exc:
        logger.error(
            "Failed to mark video %s integrity lost after frame cache mismatch: %s",
            video.video_hash,
            exc,
            exc_info=True,
        )


def _ensure_valid_frame_cache_for_frame_anonymization(video: "VideoFile") -> None:
    validation = validate_video_frame_cache(video)
    if validation.valid:
        return

    logger.warning(
        json.dumps(
            {
                "event": "frame_anonymization_cache_preflight",
                "video_hash": str(video.video_hash),
                "status": "invalid",
                **validation.as_log_payload(),
            },
            sort_keys=True,
            default=str,
        )
    )
    try:
        if not extract_video_frames(video, overwrite=False):
            raise RuntimeError("frame cache repair returned false")
    except Exception as exc:
        detail = f"frame cache repair before anonymization failed: {exc}"
        _record_frame_cache_mismatch(video, detail)
        raise RuntimeError(detail) from exc

    validation = validate_video_frame_cache(video)
    if validation.valid:
        return

    detail = "frame cache remains invalid after repair before anonymization"
    _record_frame_cache_mismatch(video, detail)
    logger.error(
        json.dumps(
            {
                "event": "frame_anonymization_cache_preflight",
                "video_hash": str(video.video_hash),
                "status": "invalid_after_repair",
                **validation.as_log_payload(),
            },
            sort_keys=True,
            default=str,
        )
    )
    raise RuntimeError(detail)


def _create_anonymized_frame_files(
    video: "VideoFile",
    anonymized_frame_dir: Path,
    endo_roi: RoiBoxCore,
    frames: "QuerySet[Frame]",
    outside_frame_numbers: set[int],
    censor_color: tuple[int, int, int] = (0, 0, 0),
) -> list[Path]:
    """
    Creates anonymized versions of frames, censoring outside the ROI or using censor_color for 'outside' frames.

    Args:
        video: The VideoFile instance.
        anonymized_frame_dir: Directory to save anonymized frames.
        endo_roi: The endoscope region of interest dictionary.
        frames: QuerySet of all Frame objects for the video.
        outside_frame_numbers: Set of frame numbers labeled as 'outside'.
        censor_color: BGR color tuple for censoring.

    Returns:
        List of paths to the generated anonymized frame files.

    Raises:
        RuntimeError: If anonymization fails for any frame.
    """
    generated_paths: list[Path] = []
    frame_iterator = frames.filter(is_extracted=True).iterator()
    total_frames = frames.filter(is_extracted=True).count()
    progress_bar = tqdm(
        frame_iterator,
        total=total_frames,
        desc=f"Anonymizing frames for {video.video_hash}",
    )

    for frame_obj in progress_bar:
        try:
            _frame_number = frame_obj.frame_number
            target_path = (
                anonymized_frame_dir / f"frame_{frame_obj.frame_number:07d}.jpg"
            )
            make_all_black = frame_obj.frame_number in outside_frame_numbers

            try:
                source_path = frame_obj.file_path
                if not source_path:
                    raise TypeError(
                        f"Frame.file_path did not return a Path object for frame {frame_obj.frame_number}"
                    )
            except (AttributeError, TypeError, Exception) as path_err:
                logger.error(
                    "Could not determine source path for Frame %d (PK: %s) using frame_obj.file_path: %s",
                    frame_obj.frame_number,
                    frame_obj.pk,
                    path_err,
                )
                raise RuntimeError(
                    f"Failed to get source path for frame {frame_obj.frame_number}"
                ) from path_err

            if not source_path.exists():
                error_msg = (
                    f"CRITICAL INCONSISTENCY: Source frame file missing for frame {frame_obj.frame_number} "
                    f"(PK: {frame_obj.pk}, Path: {source_path}) despite is_extracted=True for video {video.video_hash}. "
                    f"Halting anonymization."
                )
                logger.error(error_msg)
                raise FileNotFoundError(error_msg)

            anonymize_frame(
                raw_frame_path=source_path,
                target_frame_path=target_path,
                endo_roi=endo_roi,
                all_black=make_all_black,
                censor_color=censor_color,
            )

            generated_paths.append(target_path)
        except (
            FileNotFoundError,
            IOError,
            ValueError,
            AttributeError,
            TypeError,
            Exception,
        ) as e:
            logger.error(
                "Error anonymizing frame %d (PK: %s): %s",
                frame_obj.frame_number,
                frame_obj.pk,
                e,
                exc_info=True,
            )
            raise RuntimeError(
                f"Failed to anonymize frame {frame_obj.frame_number}"
            ) from e

    if len(generated_paths) != total_frames:
        logger.error(
            "Mismatch in generated frames count. Expected %d, got %d.",
            total_frames,
            len(generated_paths),
        )
        raise RuntimeError(
            "Anonymized frame generation resulted in incorrect number of files."
        )

    return generated_paths


def _censor_outside_frames(
    video: "VideoFile",
    outside_label_name: str = "outside",
    only_validated: bool = False,
    censor_color: tuple[int, int, int] = (0, 0, 0),
) -> bool:
    """
    Overwrites frame files marked as 'outside' with a censored version (e.g., black).
    This modifies the original raw frames directly. Use with caution. Requires frames to be extracted.
    Raises ValueError if pre-condition not met. Returns True on success, False if any frame fails.

    State Transitions:
        - Pre-condition: Requires state.frames_extracted=True.
        - Post-condition: No state changes.
    """
    logger.warning(
        "Starting direct censoring of 'outside' frames for video %s. This modifies raw frame files.",
        video.video_hash,
    )
    state = video.get_or_create_state()
    if not state.frames_extracted:
        raise ValueError(
            f"Frames not extracted for video {video.video_hash}. Cannot censor."
        )

    outside_frames = _get_outside_frames(
        video,
        outside_label_name,
        only_validated=only_validated,
    )
    if not outside_frames:
        logger.info(
            "No 'outside' frames found to censor for video %s.", video.video_hash
        )
        return True

    censored_count = 0
    error_count = 0
    for frame_obj in outside_frames:
        try:
            frame_path = frame_obj.file_path
            if not frame_path:
                logger.warning(
                    "Could not get file path for frame %d. Skipping censoring.",
                    frame_obj.frame_number,
                )
                error_count += 1
                continue

            if not frame_path.exists():
                logger.warning(
                    "Frame file %s not found for censoring. Skipping.", frame_path
                )
                error_count += 1
                continue

            anonymize_frame(
                raw_frame_path=frame_path,
                target_frame_path=frame_path,
                endo_roi=all_black_fallback_roi_box(),
                all_black=True,
                censor_color=censor_color,
            )
            censored_count += 1

        except Exception as e:
            logger.error(
                "Error censoring frame %d (%s): %s",
                frame_obj.frame_number,
                getattr(frame_obj, "relative_path", "N/A"),
                e,
                exc_info=True,
            )
            error_count += 1

    logger.info(
        "Finished censoring for video %s. Censored: %d, Errors: %d",
        video.video_hash,
        censored_count,
        error_count,
    )
    return error_count == 0


def _make_temporary_anonymized_frames(
    video: "VideoFile", roi_processing: bool = True
) -> tuple[Path, list[Path]]:
    """
    Creates temporary anonymized frames in a separate directory.
    Requires raw file and extracted frames. Raises ValueError or RuntimeError on failure.

    State Transitions:
        - Pre-condition: Requires state.frames_extracted=True (or triggers extraction).
        - Post-condition: No state changes directly by this function.
    """
    if video.is_processed:
        raise ValueError(
            f"Cannot create temporary anonymized frames for video {video.video_hash}: already processed."
        )
    if not video.has_raw:
        raise ValueError(
            f"Cannot create temporary anonymized frames for video {video.video_hash}: Raw file is missing."
        )

    temp_anonym_frame_dir = video.get_temp_anonymized_frame_dir()
    ensure_directory(temp_anonym_frame_dir)
    logger.info(
        "Creating temporary anonymized frames for video %s in %s",
        video.video_hash,
        temp_anonym_frame_dir,
    )
    if roi_processing:
        endo_roi = roi_box_or_none_from_object(video.get_endo_roi())
        if endo_roi is None or not validate_endo_roi(endo_roi):
            raise ValueError(f"Endoscope ROI is not valid for video {video.video_hash}")
    else:
        endo_roi = all_black_fallback_roi_box()

    state = video.get_or_create_state()
    if not state.frames_extracted:
        logger.info(
            "Raw frames not extracted for %s, extracting now.", video.video_hash
        )
        try:
            if not extract_video_frames(video, overwrite=False):
                raise RuntimeError(
                    f"Frame extraction method returned False unexpectedly for video {video.video_hash}."
                )
            state.refresh_from_db()
            if not state.frames_extracted:
                raise RuntimeError(
                    f"Frame extraction did not update state for video {video.video_hash}, cannot create anonymized frames."
                )
        except Exception as extract_e:
            logger.error(
                "Frame extraction failed during anonymization prep for video %s: %s",
                video.video_hash,
                extract_e,
                exc_info=True,
            )
            raise RuntimeError(
                f"Frame extraction failed for video {video.video_hash}, cannot create anonymized frames."
            ) from extract_e

    _ensure_valid_frame_cache_for_frame_anonymization(video)

    all_frames = video.get_frames()
    if not all_frames.exists():
        raise FileNotFoundError(
            f"No frame objects found for video {video.video_hash} after extraction attempt."
        )

    outside_frame_numbers = _get_outside_frame_numbers(video)

    logger.info(
        "Generating %d temporary anonymized frame files for video %s...",
        all_frames.filter(is_extracted=True).count(),
        video.video_hash,
    )
    generated_frame_paths = _create_anonymized_frame_files(
        video=video,
        anonymized_frame_dir=temp_anonym_frame_dir,
        endo_roi=endo_roi,
        frames=all_frames,
        outside_frame_numbers=outside_frame_numbers,
    )
    logger.info(
        "Generated %d temporary anonymized frame files for video %s.",
        len(generated_frame_paths),
        video.video_hash,
    )
    return temp_anonym_frame_dir, generated_frame_paths


def _merge_half_open_intervals(
    intervals: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    if not intervals:
        return []
    intervals.sort()
    merged: list[tuple[int, int]] = [intervals[0]]
    for start_frame, end_frame in intervals[1:]:
        previous_start, previous_end = merged[-1]
        if start_frame <= previous_end:
            merged[-1] = (previous_start, max(previous_end, end_frame))
        else:
            merged.append((start_frame, end_frame))
    return merged


def _outside_blackening_intervals(video: "VideoFile") -> list[tuple[int, int]]:
    intervals: list[tuple[int, int]] = []
    for segment in _get_outside_segments(video, only_validated=False):
        start_frame = int(getattr(segment, "start_frame_number", -1))
        end_frame = int(getattr(segment, "end_frame_number", -1))
        if start_frame < 0 or end_frame <= start_frame:
            logger.warning(
                "Skipping invalid outside segment for video %s: start=%s end=%s",
                video.video_hash,
                start_frame,
                end_frame,
            )
            continue
        intervals.append((start_frame, end_frame))

    from endoreg_db.models.label.annotation.image_classification import (
        ImageClassificationAnnotation,
    )

    annotated_frame_numbers = (
        ImageClassificationAnnotation.objects.filter(
            frame__video=video,
            frame__frame_number__gte=0,
            label__name__iexact="outside",
            value=True,
        )
        .values_list("frame__frame_number", flat=True)
        .distinct()
    )
    for frame_number in annotated_frame_numbers.iterator():
        start_frame = int(frame_number)
        intervals.append((start_frame, start_frame + 1))

    return _merge_half_open_intervals(intervals)


def _anonymize(video: "VideoFile", delete_original_raw: bool = True) -> bool:
    """
    Stream a raw video through FFmpeg ROI masking instead of materializing every
    frame. File-backed frames are reserved for explicit frame workflows such as
    training materialization, exports, and direct frame annotation.
    """
    state = video.get_or_create_state()

    if _video_has_integrity_failure(video):
        detail = _video_integrity_failure_detail(video) or "integrity failure"
        raise ValueError(
            f"Video {video.video_hash} is marked failed/lost and cannot be anonymized: {detail}"
        )

    if state.anonymized:
        logger.info(
            "Video %s is already marked as anonymized in state. Skipping.",
            video.video_hash,
        )
        return True
    if not video.has_raw:
        raise FileNotFoundError(
            f"Raw file is missing for video {video.video_hash}, cannot anonymize."
        )
    if not video.sensitive_meta or not video.sensitive_meta.is_verified:
        raise ValueError(
            f"Sensitive metadata for video {video.video_hash} is not validated. Cannot anonymize."
        )

    endo_roi = roi_box_or_none_from_object(video.get_endo_roi())
    if endo_roi is None or not validate_endo_roi(endo_roi):
        raise ValueError(f"Endoscope ROI is not valid for video {video.video_hash}")

    final_storage_path = video.get_target_anonymized_video_path()
    anonymized_video_path = (
        ensure_directory(
            path_utils.EndoregPathsModel.from_environment().transcoding
            / "legacy_anonymized_videos"
        )
        / final_storage_path.name
    )
    safe_cleanup_staging_file(
        anonymized_video_path,
        label="stale streamed anonymized video output",
        allowed_roots=(anonymized_video_path.parent,),
        missing_ok=True,
    )

    outside_intervals = _outside_blackening_intervals(video)
    logger.info(
        "Starting streamed anonymization for video %s with %d outside intervals.",
        video.video_hash,
        len(outside_intervals),
    )

    try:
        with cast(_LocalRawFileProvider, video).ensure_local_raw_file() as raw_path:
            streamed_path = mask_video_to_roi_and_blacken_intervals(
                Path(raw_path),
                anonymized_video_path,
                endo_roi=endo_roi,
                intervals=outside_intervals,
            )
        if streamed_path is None:
            raise RuntimeError(
                f"FFmpeg streamed anonymization failed for video {video.video_hash}."
            )
        if not anonymized_video_path.exists():
            raise RuntimeError(
                f"Processed video file not found after streamed anonymization for {video.video_hash}: {anonymized_video_path}"
            )

        new_processed_hash = get_video_hash(anonymized_video_path)
        if (
            type(video)
            .objects.filter(processed_video_hash=new_processed_hash)
            .exclude(pk=video.pk)
            .exists()
        ):
            raise ValueError(
                f"Processed video hash {new_processed_hash} already exists for another video (Video: {video.video_hash})."
            )

        original_raw_file_name_to_delete = ""
        original_raw_frame_dir_to_delete = None

        with transaction.atomic():
            video.processed_video_hash = new_processed_hash
            processed_relative_name = path_utils.to_storage_relative(final_storage_path)
            save_local_file(
                video.processed_file,
                anonymized_video_path,
                name=processed_relative_name,
                save=False,
                overwrite=True,
            )

            update_fields = [
                "processed_video_hash",
                "processed_file",
            ]

            if delete_original_raw:
                original_raw_file_name_to_delete = getattr(video.raw_file, "name", "")
                original_raw_frame_dir_to_delete = video.get_frame_dir_path()
                video.raw_file.name = ""
                update_fields.append("raw_file")
                transaction.on_commit(
                    lambda: _cleanup_raw_assets(
                        video_hash=video.video_hash,
                        raw_file_name=original_raw_file_name_to_delete,
                        raw_frame_dir=original_raw_frame_dir_to_delete,
                    )
                )

            transaction.on_commit(
                lambda: sync_video_streamable_artifacts(
                    video,
                    include_raw=not delete_original_raw,
                    include_processed=True,
                    save=True,
                )
            )

            video.save(update_fields=update_fields)
            assert video.state is not None
            video.state.mark_anonymized(save=True)

        safe_cleanup_staging_file(
            anonymized_video_path,
            label="streamed anonymized video output after storage save",
            allowed_roots=(anonymized_video_path.parent,),
            missing_ok=True,
        )
        video.refresh_from_db()
        return True

    except Exception as e:
        logger.error(
            "Streamed anonymization failed for video %s: %s",
            video.video_hash,
            e,
            exc_info=True,
        )
        safe_cleanup_staging_file(
            anonymized_video_path,
            label="streamed anonymized video output after failure",
            allowed_roots=(anonymized_video_path.parent,),
            missing_ok=True,
        )
        raise RuntimeError(f"Anonymization failed for video {video.video_hash}") from e


@transaction.atomic
def _anonymize_from_frame_cache(
    video: "VideoFile", delete_original_raw: bool = True
) -> bool:
    """
    Legacy full-frame anonymization path.

    This remains available for workflows that explicitly require file-backed
    frames, but the default VideoFile.anonymize path uses streamed FFmpeg masking.

    Raises:
        ValueError: If required preconditions are not met (e.g., frames not extracted, sensitive metadata not validated).
        FileNotFoundError: If the raw video file is missing.
        RuntimeError: If anonymization or video assembly fails.

    Returns:
        bool: True if anonymization completes successfully.
    """
    state = video.get_or_create_state()

    if _video_has_integrity_failure(video):
        detail = _video_integrity_failure_detail(video) or "integrity failure"
        raise ValueError(
            f"Video {video.video_hash} is marked failed/lost and cannot be anonymized: {detail}"
        )

    if state.anonymized:
        logger.info(
            "Video %s is already marked as anonymized in state. Skipping.",
            video.video_hash,
        )
        return True
    if not video.has_raw:
        raise FileNotFoundError(
            f"Raw file is missing for video {video.video_hash}, cannot anonymize."
        )
    if not state.frames_extracted:
        raise ValueError(
            f"Frames not extracted for video {video.video_hash}, cannot anonymize."
        )
    if not video.sensitive_meta or not video.sensitive_meta.is_verified:
        raise ValueError(
            f"Sensitive metadata for video {video.video_hash} is not validated. Cannot anonymize."
        )
    # outside_segments = video.get_outside_segments(only_validated=False)
    # unvalidated_outside = outside_segments.filter(state__is_validated=False)

    logger.info("Starting anonymization process for video %s", video.video_hash)

    temp_anonym_frame_dir = None
    anonymized_video_path = None
    try:
        temp_anonym_frame_dir, generated_frame_paths = (
            _make_temporary_anonymized_frames(video)
        )
        if not generated_frame_paths:
            raise RuntimeError(
                f"Failed to generate temporary anonymized frames for video {video.video_hash}."
            )

        final_storage_path = video.get_target_anonymized_video_path()
        anonymized_video_path = (
            ensure_directory(
                path_utils.EndoregPathsModel.from_environment().transcoding
                / "legacy_anonymized_videos"
            )
            / final_storage_path.name
        )

        safe_cleanup_staging_file(
            anonymized_video_path,
            label="stale legacy anonymized video output",
            allowed_roots=(anonymized_video_path.parent,),
            missing_ok=True,
        )

        fps = video.get_fps()

        logger.info(
            "Assembling anonymized video for %s at %s",
            video.video_hash,
            anonymized_video_path,
        )
        assemble_video_from_frames(
            frame_paths=generated_frame_paths,
            output_path=anonymized_video_path,
            fps=fps,
        )

        if not anonymized_video_path.exists():
            raise RuntimeError(
                f"Processed video file not found after assembly for {video.video_hash}: {anonymized_video_path}"
            )

        new_processed_hash = get_video_hash(anonymized_video_path)
        if (
            type(video)
            .objects.filter(processed_video_hash=new_processed_hash)
            .exclude(pk=video.pk)
            .exists()
        ):
            raise ValueError(
                f"Processed video hash {new_processed_hash} already exists for another video (Video: {video.video_hash})."
            )

        video.processed_video_hash = new_processed_hash
        processed_relative_name = path_utils.to_storage_relative(final_storage_path)
        save_local_file(
            video.processed_file,
            anonymized_video_path,
            name=processed_relative_name,
            save=False,
            overwrite=True,
        )
        safe_cleanup_staging_file(
            anonymized_video_path,
            label="legacy anonymized video output after storage save",
            allowed_roots=(anonymized_video_path.parent,),
            missing_ok=True,
        )

        update_fields = [
            "processed_video_hash",
            "processed_file",
            "frame_dir",
        ]

        if delete_original_raw:
            original_raw_file_name_to_delete = getattr(video.raw_file, "name", "")
            original_raw_frame_dir_to_delete = video.get_frame_dir_path()

            video.raw_file.name = ""

            update_fields.extend(["raw_file", "video_hash"])

            transaction.on_commit(
                lambda: _cleanup_raw_assets(
                    video_hash=video.video_hash,
                    raw_file_name=original_raw_file_name_to_delete,
                    raw_frame_dir=original_raw_frame_dir_to_delete,
                )
            )

        transaction.on_commit(
            lambda: sync_video_streamable_artifacts(
                video,
                include_raw=not delete_original_raw,
                include_processed=True,
                save=True,
            )
        )

        video.save(update_fields=update_fields)
        assert video.state is not None  # For type checker
        video.state.mark_anonymized(save=True)
        video.refresh_from_db()
        return True

    except Exception as e:
        logger.error(
            "Anonymization failed for video %s: %s", video.video_hash, e, exc_info=True
        )
        if anonymized_video_path and anonymized_video_path.exists():
            logger.warning(
                "Cleaning up potentially orphaned processed file for video %s due to error: %s",
                video.video_hash,
                anonymized_video_path,
            )
            safe_cleanup_staging_file(
                anonymized_video_path,
                label="legacy anonymized video output after failure",
                allowed_roots=(anonymized_video_path.parent,),
                missing_ok=True,
            )
        raise RuntimeError(f"Anonymization failed for video {video.video_hash}") from e

    finally:
        if temp_anonym_frame_dir and temp_anonym_frame_dir.exists():
            logger.info(
                "Cleaning up temporary anonymized frame directory for video %s: %s",
                video.video_hash,
                temp_anonym_frame_dir,
            )
            safe_rmtree(temp_anonym_frame_dir)


def _cleanup_raw_assets(
    video_hash: "str",
    raw_file_name: str = "",
    raw_file_path: Optional[Path] = None,
    raw_frame_dir: Optional[Path] = None,
):
    """
    Deletes the original raw video file and its extracted frames directory.
    Called via transaction.on_commit after successful anonymization.

    State Transitions:
        - Sets state.frames_extracted=False.
    """
    from endoreg_db.models.media.video.video_file import VideoFile

    logger.info(
        "Performing post-commit cleanup of raw assets for video %s.", video_hash
    )
    try:
        video_file = (
            VideoFile.objects.select_related("state")
            .filter(video_hash=video_hash)
            .first()
        )
        if not video_file:
            logger.error(
                "VideoFile %s not found during post-commit cleanup.", video_hash
            )
            return
        if not video_file.state:
            logger.error(
                "VideoState not found for VideoFile %s during post-commit cleanup.",
                video_hash,
            )
        state = video_file.get_or_create_state()

        if raw_file_name:
            logger.info(
                "Deleting original raw video FieldFile through storage: %s",
                raw_file_name,
            )
            video_file.raw_file.name = raw_file_name
            video_file.raw_file.delete(save=False)
        elif raw_file_path and raw_file_path.exists():
            logger.info("Deleting original raw video path: %s", raw_file_path)
            safe_unlink_file(raw_file_path, missing_ok=True)

        if raw_frame_dir and raw_frame_dir.exists():
            logger.info("Deleting original raw frame directory: %s", raw_frame_dir)
            safe_rmtree(raw_frame_dir)
        elif raw_frame_dir:
            logger.warning(
                "Original raw frame directory %s not found for post-commit deletion.",
                raw_frame_dir,
            )

        if state.frames_extracted:
            state.frames_extracted = False
            state.save(update_fields=["frames_extracted"])
            logger.info(
                "Set state.frames_extracted=False for video %s after raw asset cleanup.",
                video_hash,
            )

    except Exception as e:
        logger.error(
            "Error during post-commit cleanup of raw assets for video %s: %s",
            video_hash,
            e,
            exc_info=True,
        )

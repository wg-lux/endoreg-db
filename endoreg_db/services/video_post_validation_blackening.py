from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label_video_segment import LabelVideoSegment
from endoreg_db.services.media_operation_gate import (
    MediaOperationDeferred,
    defer_if_video_media_busy,
)
from endoreg_db.services.streamable_media import sync_video_streamable_artifacts
from endoreg_db.services.video_files.io import ensure_local_processed_video_file
from endoreg_db.utils.filesystem.file_operations import (
    ensure_directory,
    safe_unlink_file,
)
from endoreg_db.utils.security.hashs import get_video_hash
from endoreg_db.utils.filesystem.paths import (
    ANONYM_VIDEO_DIR,
    data_paths,
    to_storage_relative,
)
from endoreg_db.utils.storage import save_local_file
from endoreg_db.utils.video.ffmpeg_wrapper import blacken_video_frame_intervals

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)

__all__ = [
    "merge_outside_frame_intervals",
    "rebuild_processed_video_without_outside_frames",
]


def merge_outside_frame_intervals(
    video: VideoFile,
    *,
    only_validated: bool = False,
) -> list[tuple[int, int]]:
    """
    Return sorted, merged half-open frame intervals that must be blackened.

    LabelVideoSegment ranges in this codebase are [start_frame_number,
    end_frame_number). Frame-level outside annotations are represented as
    one-frame intervals.
    """
    intervals: list[tuple[int, int]] = []
    segments = LabelVideoSegment.objects.filter(
        video_file=video,
        label__name__iexact="outside",
    )
    if only_validated:
        segments = segments.filter(state__is_validated=True)

    for segment in segments:
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


def rebuild_processed_video_without_outside_frames(
    video: VideoFile,
    *,
    only_validated: bool = False,
    outside_intervals: Sequence[tuple[int, int]] | None = None,
) -> bool:
    """
    Rebuild the processed video by blackening frames in outside intervals.

    The rebuilt artifact replaces ``video.processed_file`` only after FFmpeg
    succeeds, the new processed hash is unique, and no active media lease blocks
    the swap.
    """
    staged_output_path: Path | None = None
    replace_completed = False

    if not video or not video.is_processed:
        logger.warning(
            "No processed video file available for VideoFile %s.",
            getattr(video, "video_hash", "<unknown>"),
        )
        return False

    intervals = (
        list(outside_intervals)
        if outside_intervals is not None
        else merge_outside_frame_intervals(
            video,
            only_validated=only_validated,
        )
    )
    if not intervals:
        logger.info(
            "No applicable outside segments found for video %s. Skipping rebuild.",
            video.video_hash,
        )
        return True

    try:
        with ensure_local_processed_video_file(video) as processed_path:
            transcoding_dir = ensure_directory(Path(data_paths["transcoding"]))
            staged_output_path = (
                transcoding_dir
                / f"{video.video_hash}.outside_frame_blackening.staged.mp4"
            )
            safe_unlink_file(staged_output_path, missing_ok=True)
            rebuilt_path = blacken_video_frame_intervals(
                processed_path,
                staged_output_path,
                intervals=intervals,
            )
            if rebuilt_path is None:
                raise AssertionError("Failed to rebuild processed video with FFmpeg.")

            new_processed_hash = get_video_hash(rebuilt_path)
            if (
                type(video)
                .objects.filter(processed_video_hash=new_processed_hash)
                .exclude(pk=video.pk)
                .exists()
            ):
                raise ValueError(
                    "Processed video hash already exists for another video."
                )

            defer_if_video_media_busy(video_id=video.pk)
            target_path = (
                Path(ANONYM_VIDEO_DIR)
                / f"{video.video_hash}.post_validation.{new_processed_hash}.mp4"
            )
            target_name = to_storage_relative(target_path)
            save_local_file(
                video.processed_file,
                rebuilt_path,
                name=target_name,
                save=False,
                overwrite=True,
            )
            video.processed_video_hash = new_processed_hash
            video.save(
                update_fields=[
                    "processed_file",
                    "processed_video_hash",
                    "date_modified",
                ]
            )
            try:
                sync_video_streamable_artifacts(
                    video,
                    include_raw=False,
                    include_processed=True,
                    save=True,
                )
            except Exception as exc:
                logger.warning(
                    "Could not synchronize processed streamable artifact for video %s: %s",
                    video.pk,
                    exc,
                )
            replace_completed = True
            return True
    except AssertionError as ae:
        logger.error(
            "Assertion error while streaming outside-frame rebuild for VideoFile %s: %s",
            video.video_hash,
            ae,
            exc_info=True,
        )
        return False
    except MediaOperationDeferred:
        raise
    except Exception as e:
        logger.error(
            "Error creating video without 'outside' frames for VideoFile %s: %s",
            video.video_hash,
            e,
            exc_info=True,
        )
        return False
    finally:
        if staged_output_path is not None:
            if replace_completed:
                logger.info(
                    "Cleaning up staged outside-frame rebuild output for video %s: %s",
                    video.video_hash,
                    staged_output_path,
                )
            else:
                logger.warning(
                    "Cleaning failed staged outside-frame rebuild output for video %s: %s",
                    video.video_hash,
                    staged_output_path,
                )
            safe_unlink_file(staged_output_path, missing_ok=True)

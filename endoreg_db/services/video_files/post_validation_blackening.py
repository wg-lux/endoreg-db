from __future__ import annotations

from endoreg_db.utils.storage.files import canonical_media_name

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.services.media.operation_gate import (
    MediaOperationDeferred,
    defer_if_video_media_busy,
    video_artifact_mutation,
    video_artifact_publication,
)
from endoreg_db.services.streaming.hls_media import (
    hls_result_is_ready,
    materialize_video_hls,
)
from endoreg_db.services.streaming.streamable_media import (
    sync_video_streamable_artifacts,
)
from endoreg_db.services.video_files.io import ensure_local_processed_video_file
from endoreg_db.services.video_storage.generation_cleanup import (
    commit_processed_replacements,
    record_processed_replacement,
    reconcile_previous_processed_cleanup,
    schedule_processed_generation_cleanup,
)
from endoreg_db.services.video_storage.workflow import (
    configured_video_storage_profile,
    evidence_as_json,
    probe_video_artifact,
    segment_timeline_references,
    validate_normalized_output,
)
from endoreg_db.utils.ffmpeg_wrapper import blacken_video_frame_intervals
from endoreg_db.utils.file_operations import (
    ensure_directory,
    atomic_create_file,
    safe_unlink_file,
    safe_delete_field_file,
)
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.paths import to_storage_relative, get_runtime_paths
from endoreg_db.utils.storage import save_local_file

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
                video.raw_video_hash,
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
    """Serialize replacement, playback publication and generation retirement."""
    with video_artifact_mutation(video_id=int(video.pk)):
        video.refresh_from_db()
        return _rebuild_processed_video_owned(
            video, only_validated=only_validated, outside_intervals=outside_intervals
        )


def _rebuild_processed_video_owned(
    video: VideoFile,
    *,
    only_validated: bool,
    outside_intervals: Sequence[tuple[int, int]] | None,
) -> bool:
    """
    Rebuild the processed video by blackening frames in outside intervals.

    The rebuilt artifact replaces ``video.processed_file`` only after FFmpeg
    succeeds, the new processed hash is unique, and no active media lease blocks
    the swap.
    """
    staged_output_path: Path | None = None
    replace_completed = False
    published = False
    candidate_name = ""
    previous_name = str(video.processed_file.name or "")
    previous_hash = video.processed_video_hash

    if not video or not video.is_processed:
        logger.warning(
            "No processed video file available for VideoFile %s.",
            getattr(video, "raw_video_hash", "<unknown>"),
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
            video.raw_video_hash,
        )
        return True

    try:
        reconcile_previous_processed_cleanup(video)
        with ensure_local_processed_video_file(video) as processed_path:
            if get_file_hash(processed_path) != previous_hash:
                raise ValueError(
                    "Processed source digest differs from its stored identity."
                )
            source_probe = probe_video_artifact(processed_path)
            transcoding_dir = ensure_directory(get_runtime_paths().transcoding)
            staged_output_path = (
                transcoding_dir
                / f"{video.raw_video_hash}.outside_frame_blackening.{uuid4().hex}.mp4"
            )
            atomic_create_file(
                destination=staged_output_path, content=(), file_mode=0o600
            )
            rebuilt_path = blacken_video_frame_intervals(
                processed_path,
                staged_output_path,
                intervals=intervals,
            )
            if rebuilt_path is None:
                raise AssertionError("Failed to rebuild processed video with FFmpeg.")

            normalization_evidence = validate_normalized_output(
                source=source_probe,
                output=probe_video_artifact(rebuilt_path),
                profile=configured_video_storage_profile(),
                segments=segment_timeline_references(
                    video, timeline=source_probe.timeline
                ),
            )
            new_processed_hash = get_file_hash(rebuilt_path)
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
            target_path = get_runtime_paths().anonym_video / canonical_media_name(
                video.raw_video_hash, ".mp4", generation=uuid4().hex
            )
            target_name = to_storage_relative(target_path)
            save_local_file(
                video.processed_file,
                rebuilt_path,
                name=target_name,
                save=False,
            )
            candidate_name = str(video.processed_file.name or "")
            if video.processed_file.get_hash() != new_processed_hash:
                raise ValueError("Encrypted processed candidate identity mismatch.")
            with video_artifact_publication(video_id=int(video.pk)):
                video.processed_video_hash = new_processed_hash
                video.meta = {
                    **(video.meta or {}),
                    "storage_normalization": evidence_as_json(normalization_evidence),
                }
                record_processed_replacement(
                    video,
                    previous_name=previous_name,
                    previous_hash=previous_hash,
                    strict=True,
                )
                video.save(
                    update_fields=[
                        "processed_file",
                        "processed_video_hash",
                        "meta",
                        "date_modified",
                    ]
                )
            published = True
        # Drop both plaintext paths before the HLS encoder materializes its source.
        safe_unlink_file(staged_output_path, missing_ok=True)
        result = materialize_video_hls(
            int(video.pk), artifact_kind="processed", claim_queued=True
        )
        if not hls_result_is_ready(result.status):
            raise RuntimeError(
                f"Processed HLS materialization ended with {result.status}."
            )
        sync_video_streamable_artifacts(
            video,
            include_raw=False,
            include_processed=True,
            save=True,
        )
        with video_artifact_publication(video_id=int(video.pk)):
            commit_processed_replacements(video)
            video.save(update_fields=["meta", "date_modified"])
        schedule_processed_generation_cleanup(int(video.pk))
        replace_completed = True
        return True
    except AssertionError as ae:
        logger.error(
            "Assertion error while streaming outside-frame rebuild for VideoFile %s: %s",
            video.raw_video_hash,
            ae,
            exc_info=True,
        )
        return False
    except MediaOperationDeferred:
        raise
    except Exception as e:
        logger.error(
            "Error creating video without 'outside' frames for VideoFile %s: %s",
            video.raw_video_hash,
            e,
            exc_info=True,
        )
        return False
    finally:
        if not published and candidate_name:
            safe_delete_field_file(video.processed_file, missing_ok=True)
            video.processed_file.name = previous_name
            video.processed_video_hash = previous_hash
        if staged_output_path is not None:
            if replace_completed:
                logger.info(
                    "Cleaning up staged outside-frame rebuild output for video %s: %s",
                    video.raw_video_hash,
                    staged_output_path,
                )
            else:
                logger.warning(
                    "Cleaning failed staged outside-frame rebuild output for video %s: %s",
                    video.raw_video_hash,
                    staged_output_path,
                )
            safe_unlink_file(staged_output_path, missing_ok=True)

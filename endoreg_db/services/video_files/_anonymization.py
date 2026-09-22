# pyright: reportUnusedFunction=false, reportPrivateUsage=false, reportMissingTypeStubs=false

import logging
from collections.abc import Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from uuid import uuid4
from typing import TYPE_CHECKING, Optional, Protocol, cast
from lx_dtypes.models.contracts.endoscopy_processor import (
    roi_box_or_none_from_object,
)
from django.db import transaction

from endoreg_db.import_files.file_storage.cleanup import safe_cleanup_staging_file
from endoreg_db.services.streamable_media import sync_video_streamable_artifacts
from endoreg_db.import_files.file_storage.state_management import ensure_video_hls
from endoreg_db.services.processed_video_cleanup import (
    commit_processed_replacements,
    record_processed_replacement,
    reconcile_previous_processed_cleanup,
    schedule_processed_generation_cleanup,
)
from endoreg_db.services.video_storage_normalization import (
    configured_video_storage_profile,
    assert_temporal_equivalence,
    timeline_from_video_metadata,
    evidence_as_json,
    probe_video_artifact,
    segment_timeline_references,
    validate_normalized_output,
)
from endoreg_db.utils.file_operations import get_file_hash
from endoreg_db.utils.paths import get_runtime_paths, to_storage_relative
from endoreg_db.utils.storage.files import canonical_media_name
from endoreg_db.utils.file_operations import (
    safe_rmtree,
    safe_delete_field_file,
    safe_unlink_file,
)
from endoreg_db.utils.storage import save_local_file
from endoreg_db.utils.validate_endo_roi import validate_endo_roi

from endoreg_db.utils.ffmpeg_wrapper import mask_video_to_roi_and_blacken_intervals
from endoreg_db.services.video_files._segments import (
    _get_outside_segments,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)


class _LocalRawFileProvider(Protocol):
    def ensure_local_raw_file(self) -> AbstractContextManager[Path]: ...


def _video_integrity_failure_detail(video: "VideoFile") -> str:
    payload: Mapping[str, object] = video.meta if video.meta is not None else {}
    detail = str(payload.get("integrity_error") or "").strip()
    if detail:
        return detail
    if bool(getattr(getattr(video, "state", None), "processing_error", False)):
        return "video state is marked failed/lost"
    return ""


def _video_has_integrity_failure(video: "VideoFile") -> bool:
    payload: Mapping[str, object] = video.meta if video.meta is not None else {}
    return payload.get("integrity_status") == "lost" or bool(
        getattr(getattr(video, "state", None), "processing_error", False)
    )


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
                video.raw_video_hash,
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
    """Acquire storage ownership before any anonymization work or transaction."""
    from endoreg_db.services.media_operation_gate import video_artifact_mutation

    with video_artifact_mutation(video_id=int(video.pk)):
        return _anonymize_owned(video, delete_original_raw=delete_original_raw)


def _anonymize_owned(video: "VideoFile", delete_original_raw: bool = True) -> bool:
    """
    Stream a raw video through FFmpeg ROI masking instead of materializing every
    frame. File-backed frames are reserved for explicit frame workflows such as
    exports. Direct frame annotation decodes the processed video in memory.
    """
    state = video.get_or_create_state()

    if _video_has_integrity_failure(video):
        detail = _video_integrity_failure_detail(video) or "integrity failure"
        raise ValueError(
            f"Video {video.raw_video_hash} is marked failed/lost and cannot be anonymized: {detail}"
        )

    if state.anonymized:
        logger.info(
            "Video %s is already marked as anonymized in state. Skipping.",
            video.raw_video_hash,
        )
        return True
    if not video.has_raw:
        raise FileNotFoundError(
            f"Raw file is missing for video {video.raw_video_hash}, cannot anonymize."
        )
    if not video.sensitive_meta or not video.sensitive_meta.is_verified:
        raise ValueError(
            f"Sensitive metadata for video {video.raw_video_hash} is not validated. Cannot anonymize."
        )

    endo_roi = roi_box_or_none_from_object(video.get_endo_roi())
    if endo_roi is None or not validate_endo_roi(endo_roi):
        raise ValueError(f"Endoscope ROI is not valid for video {video.raw_video_hash}")

    reconcile_previous_processed_cleanup(video)
    previous_name = str(video.processed_file.name or "")
    previous_hash = video.processed_video_hash
    generation_name = canonical_media_name(
        video.raw_video_hash, ".mp4", generation=uuid4().hex
    )
    final_storage_path = get_runtime_paths().anonym_video / generation_name
    anonymized_video_path = get_runtime_paths().transcoding / generation_name
    safe_cleanup_staging_file(
        anonymized_video_path,
        label="stale streamed anonymized video output",
        allowed_roots=(anonymized_video_path.parent,),
        missing_ok=True,
    )

    outside_intervals = _outside_blackening_intervals(video)
    logger.info(
        "Starting streamed anonymization for video %s with %d outside intervals.",
        video.raw_video_hash,
        len(outside_intervals),
    )

    published = False
    candidate_name = ""
    try:
        with cast(_LocalRawFileProvider, video).ensure_local_raw_file() as raw_path:
            source_probe = probe_video_artifact(Path(raw_path))
            if video.fps is None or video.duration is None or video.frame_count is None:
                raise RuntimeError(
                    "Stored video timeline is required for reanonymization."
                )
            assert_temporal_equivalence(
                timeline_from_video_metadata(
                    fps=float(video.fps),
                    duration_seconds=float(video.duration),
                    frame_count=int(video.frame_count),
                ),
                source_probe.timeline,
                profile=configured_video_storage_profile(),
            )
            streamed_path = mask_video_to_roi_and_blacken_intervals(
                Path(raw_path),
                anonymized_video_path,
                endo_roi=endo_roi,
                intervals=outside_intervals,
            )
        if streamed_path is None:
            raise RuntimeError(
                f"FFmpeg streamed anonymization failed for video {video.raw_video_hash}."
            )
        if not anonymized_video_path.exists():
            raise RuntimeError(
                f"Processed video file not found after streamed anonymization for {video.raw_video_hash}: {anonymized_video_path}"
            )

        normalization_evidence = validate_normalized_output(
            source=source_probe,
            output=probe_video_artifact(anonymized_video_path),
            profile=configured_video_storage_profile(),
            segments=segment_timeline_references(video, timeline=source_probe.timeline),
        )
        new_processed_hash = get_file_hash(anonymized_video_path)
        if (
            type(video)
            .objects.filter(processed_video_hash=new_processed_hash)
            .exclude(pk=video.pk)
            .exists()
        ):
            raise ValueError(
                f"Processed video hash {new_processed_hash} already exists for another video (Video: {video.raw_video_hash})."
            )

        original_raw_file_name_to_delete = ""
        original_raw_frame_dir_to_delete = None

        processed_relative_name = to_storage_relative(final_storage_path)
        save_local_file(
            video.processed_file,
            anonymized_video_path,
            name=processed_relative_name,
            save=False,
        )
        candidate_name = str(video.processed_file.name or "")
        if video.processed_file.get_hash() != new_processed_hash:
            raise RuntimeError("Encrypted anonymized candidate identity mismatch.")
        with transaction.atomic():
            video.processed_video_hash = new_processed_hash
            video.meta = {
                **(video.meta or {}),
                "storage_normalization": evidence_as_json(normalization_evidence),
            }
            if previous_name:
                record_processed_replacement(
                    video,
                    previous_name=previous_name,
                    previous_hash=previous_hash,
                    strict=True,
                )
            video.save(update_fields=["processed_video_hash", "processed_file", "meta"])
        published = True

        # The writer lease spans HLS work, without a long publication transaction.
        ensure_video_hls(video, force=True)
        sync_video_streamable_artifacts(
            video,
            include_raw=True,
            include_processed=True,
            save=True,
        )
        with transaction.atomic():
            cleanup_pending = commit_processed_replacements(video)
            update_fields = ["meta"]
            if delete_original_raw:
                original_raw_file_name_to_delete = getattr(video.raw_file, "name", "")
                original_raw_frame_dir_to_delete = video.get_frame_dir_path()
                video.raw_file.name = ""
                update_fields.append("raw_file")
                transaction.on_commit(
                    lambda: _cleanup_raw_assets(
                        raw_video_hash=video.raw_video_hash,
                        raw_file_name=original_raw_file_name_to_delete,
                        raw_frame_dir=original_raw_frame_dir_to_delete,
                    )
                )
            video.save(update_fields=update_fields)
            assert video.state is not None
            video.state.mark_anonymized(save=True)
        if cleanup_pending:
            schedule_processed_generation_cleanup(int(video.pk))

        video.refresh_from_db()
        return True

    except Exception as e:
        if not published and candidate_name and candidate_name != previous_name:
            safe_delete_field_file(video.processed_file, missing_ok=True)
            video.processed_file.name = previous_name
            video.processed_video_hash = previous_hash
        logger.error(
            "Streamed anonymization failed for video %s: %s",
            video.raw_video_hash,
            e,
            exc_info=True,
        )
        raise RuntimeError(
            f"Anonymization failed for video {video.raw_video_hash}"
        ) from e
    finally:
        safe_cleanup_staging_file(
            anonymized_video_path,
            label="streamed anonymized video output",
            allowed_roots=(anonymized_video_path.parent,),
            missing_ok=True,
        )


def _cleanup_raw_assets(
    raw_video_hash: "str",
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
        "Performing post-commit cleanup of raw assets for video %s.", raw_video_hash
    )
    try:
        video_file = (
            VideoFile.objects.select_related("state")
            .filter(raw_video_hash=raw_video_hash)
            .first()
        )
        if not video_file:
            logger.error(
                "VideoFile %s not found during post-commit cleanup.", raw_video_hash
            )
            return
        if not video_file.state:
            logger.error(
                "VideoState not found for VideoFile %s during post-commit cleanup.",
                raw_video_hash,
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
                raw_video_hash,
            )

    except Exception as e:
        logger.error(
            "Error during post-commit cleanup of raw assets for video %s: %s",
            raw_video_hash,
            e,
            exc_info=True,
        )

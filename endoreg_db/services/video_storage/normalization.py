from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from endoreg_db.schemas.video_storage import (
    SegmentTimelineReference,
    VideoArtifactProbe,
    VideoStorageNormalizationEvidence,
)
from endoreg_db.services.video_storage.contracts import (
    VideoStorageNormalizationError,
    VideoStorageProfile,
)
from endoreg_db.utils import ffmpeg_wrapper
from endoreg_db.utils.filesystem.file_operations import (
    atomic_move_file,
    safe_unlink_file,
)
from endoreg_db.utils.structured_logging import emit_structured_event, path_reference

logger = logging.getLogger(__name__)


class _ValidateOutput(Protocol):
    def __call__(
        self,
        *,
        source: VideoArtifactProbe,
        output: VideoArtifactProbe,
        profile: VideoStorageProfile,
        segments: list[SegmentTimelineReference] | None,
    ) -> VideoStorageNormalizationEvidence: ...


def normalize_video_file(
    *,
    input_path: Path,
    reference_path: Path,
    quality_mode: str,
    profile: VideoStorageProfile,
    segments: list[SegmentTimelineReference] | None,
    force_cpu: bool,
    probe_artifact: Callable[[Path], VideoArtifactProbe],
    validate_output: _ValidateOutput,
) -> VideoStorageNormalizationEvidence:
    """Normalize one file atomically and leave the input untouched on failure."""
    source_probe = probe_artifact(reference_path)
    current_probe = probe_artifact(input_path)
    try:
        return validate_output(
            source=source_probe,
            output=current_probe,
            profile=profile,
            segments=segments,
        )
    except VideoStorageNormalizationError:
        pass

    staging_path = input_path.with_name(
        f".{input_path.stem}.storage-normalization.{uuid4().hex}.part.mp4"
    )
    emit_structured_event(
        logger,
        "video_storage.normalization_staging_started",
        input_path=path_reference(input_path),
        reference_path=path_reference(reference_path),
        staging_path=path_reference(staging_path),
        profile_name=profile.name,
    )
    try:
        result = ffmpeg_wrapper.transcode_video(
            input_path=input_path,
            output_path=staging_path,
            quality_mode=quality_mode,
            force_cpu=force_cpu,
            extra_args=profile.ffmpeg_output_args(),
        )
        if result is None or Path(result) != staging_path:
            raise VideoStorageNormalizationError(
                "FFmpeg did not produce the expected storage-normalized output"
            )
        output_probe = probe_artifact(staging_path)
        evidence = validate_output(
            source=source_probe,
            output=output_probe,
            profile=profile,
            segments=segments,
        )
        emit_structured_event(
            logger,
            "video_storage.normalization_candidate_validated",
            input_path=path_reference(input_path),
            staging_path=path_reference(staging_path),
            profile_name=profile.name,
        )
        atomic_move_file(source=staging_path, destination=input_path)
        emit_structured_event(
            logger,
            "video_storage.normalization_published",
            input_path=path_reference(input_path),
            profile_name=profile.name,
        )
        return evidence
    except BaseException as exc:
        emit_structured_event(
            logger,
            "video_storage.normalization_failed",
            input_path=path_reference(input_path),
            staging_path=path_reference(staging_path),
            profile_name=profile.name,
            error_type=type(exc).__name__,
            detail=str(exc),
        )
        raise
    finally:
        safe_unlink_file(staging_path, missing_ok=True)

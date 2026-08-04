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
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
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


def ensure_video_file_profile(
    *,
    input_path: Path,
    output_path: Path,
    reference_path: Path,
    quality_mode: str,
    profile: VideoStorageProfile,
    segments: list[SegmentTimelineReference] | None,
    force_cpu: bool,
    probe_artifact: Callable[[Path], VideoArtifactProbe],
    validate_output: _ValidateOutput,
) -> VideoStorageNormalizationEvidence:
    """Publish a profile-compliant copy, transcoding only when required."""
    source_probe = probe_artifact(reference_path)
    current_probe = probe_artifact(input_path)
    current_evidence: VideoStorageNormalizationEvidence | None = None
    try:
        current_evidence = validate_output(
            source=source_probe,
            output=current_probe,
            profile=profile,
            segments=segments,
        )
    except VideoStorageNormalizationError:
        pass

    if current_evidence is not None and input_path == output_path:
        emit_structured_event(
            logger,
            "video_storage.profile_already_compliant",
            input_path=path_reference(input_path),
            output_path=path_reference(output_path),
            profile_name=profile.name,
        )
        return current_evidence

    staging_path = output_path.with_name(
        f".{output_path.stem}.storage-normalization.{uuid4().hex}.part.mp4"
    )
    emit_structured_event(
        logger,
        "video_storage.normalization_staging_started",
        input_path=path_reference(input_path),
        output_path=path_reference(output_path),
        reference_path=path_reference(reference_path),
        staging_path=path_reference(staging_path),
        profile_name=profile.name,
        action="copy" if current_evidence is not None else "transcode",
    )
    try:
        if current_evidence is not None:
            atomic_copy_file(source=input_path, destination=staging_path)
        else:
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
        atomic_move_file(source=staging_path, destination=output_path)
        emit_structured_event(
            logger,
            "video_storage.normalization_published",
            input_path=path_reference(input_path),
            output_path=path_reference(output_path),
            profile_name=profile.name,
            action="copied" if current_evidence is not None else "transcoded",
        )
        return evidence
    except BaseException as exc:
        emit_structured_event(
            logger,
            "video_storage.normalization_failed",
            input_path=path_reference(input_path),
            output_path=path_reference(output_path),
            staging_path=path_reference(staging_path),
            profile_name=profile.name,
            error_type=type(exc).__name__,
            detail=str(exc),
        )
        raise
    finally:
        safe_unlink_file(staging_path, missing_ok=True)


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
    """Normalize one file in place and leave it untouched on failure."""
    return ensure_video_file_profile(
        input_path=input_path,
        output_path=input_path,
        reference_path=reference_path,
        quality_mode=quality_mode,
        profile=profile,
        segments=segments,
        force_cpu=force_cpu,
        probe_artifact=probe_artifact,
        validate_output=validate_output,
    )

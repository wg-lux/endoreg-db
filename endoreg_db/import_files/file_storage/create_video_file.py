# endoreg_db/import_files/storage/create_video_file.py
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.context.ensure_center import ensure_center
from endoreg_db.utils.filesystem.file_operations import sha256_file
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)
from endoreg_db.import_files.file_storage.state_management import finalize_failure
from endoreg_db.services.hub.media_integrity import (
    MediaIntegrityExpectation,
    check_video_media_integrity,
    video_integrity_failure_allows_existing_video_reprocessing,
)
from endoreg_db.services.video_files import (
    get_video_by_content_hash,
)

logger = logging.getLogger(__name__)


class _NamedCenter(Protocol):
    name: str


@dataclass(frozen=True)
class _HistoryDecision:
    processed: bool
    needs_processing: bool
    can_short_circuit: bool = False


def _context_source_path(ctx: ImportContext) -> Path:
    return ctx.sensitive_path if isinstance(ctx.sensitive_path, Path) else ctx.file_path


def _ensure_context_file_hash(ctx: ImportContext) -> str:
    if not isinstance(ctx.file_hash, str):
        ctx.file_hash = sha256_file(ctx.file_path)
    return ctx.file_hash


def _load_current_video(ctx: ImportContext, file_hash: str) -> VideoFile | None:
    if isinstance(ctx.current_video, VideoFile):
        return ctx.current_video
    try:
        ctx.current_video = get_video_by_content_hash(file_hash)
    except VideoFile.DoesNotExist:
        ctx.current_video = None
    return ctx.current_video if isinstance(ctx.current_video, VideoFile) else None


def _handle_success_history(ctx: ImportContext, file_hash: str) -> _HistoryDecision:
    logger.info(
        "VideoFile already has successful processing history (file_hash=%s) "
        "- checking media integrity before short-circuiting",
        file_hash,
    )
    existing_video = _load_current_video(ctx, file_hash)
    integrity_result = check_video_media_integrity(
        existing_video,
        expectation=MediaIntegrityExpectation.RAW_WATCHER_VIDEO,
        content_hash=file_hash,
    )
    if integrity_result.ok:
        return _HistoryDecision(
            processed=True,
            needs_processing=False,
            can_short_circuit=True,
        )

    logger.warning(
        "Successful processing history exists for %s but media integrity "
        "failed in create_or_retrieve_video_file: %s. Continuing "
        "reimport so the processed artifact can be repaired.",
        file_hash,
        integrity_result.reason,
    )
    if isinstance(
        ctx.current_video, VideoFile
    ) and not video_integrity_failure_allows_existing_video_reprocessing(
        integrity_result
    ):
        ctx.current_video = None
    return _HistoryDecision(processed=False, needs_processing=True)


def _handle_failure_history(ctx: ImportContext, file_hash: str) -> None:
    if _load_current_video(ctx, file_hash) is None:
        logger.warning(
            "Failed ProcessingHistory exists for %s but no VideoFile could be "
            "loaded; continuing with a fresh import.",
            file_hash,
        )
        return
    finalize_failure(ctx)


def _get_or_create_video_instance(
    ctx: ImportContext,
    *,
    file_path: Path,
    file_hash: str,
) -> VideoFile:
    if isinstance(ctx.current_video, VideoFile):
        video = ctx.current_video
        logger.info("Using existing VideoFile from context: pk=%s", video.pk)
        return video

    logger.info(
        "Creating new VideoFile from %s for center %s",
        file_path,
        ctx.center_name,
    )
    video = VideoFile.create_from_file_initialized(
        file_path=file_path,
        center_name=ctx.center_name,
        processor_name=ctx.processor_name,
        video_hash=file_hash,
        initialize=not bool(getattr(ctx, "defer_video_initialization", False)),
    )

    center = cast(_NamedCenter, ensure_center(video, ctx.center_name))
    center_name = str(center.name)
    logger.info("Successfully set up video file from %s", center_name)
    return video


def _record_processing_attempt(file_hash: str, *, has_success_history: bool) -> None:
    if not has_success_history:
        ProcessingHistory.get_or_create_for_hash(
            file_hash=file_hash,
            success=False,
        )
        return

    logger.info(
        "Reprocessing video hash %s to repair failed media integrity without "
        "downgrading successful ProcessingHistory before finalization.",
        file_hash,
    )


def create_or_retrieve_video_file(
    ctx: ImportContext,
) -> tuple[VideoFile, bool, bool]:
    """
    Create a new or retrieve an existing VideoFile for the given context.

    Returns:
        video           : VideoFile instance
        processed       : True if there is already a successful ProcessingHistory for this file
        needs_processing: True if the pipeline should run for this file in this call
    """
    file_path = _context_source_path(ctx)
    file_type = ctx.file_type  # logical key for history; can be None
    file_hash = _ensure_context_file_hash(ctx)
    processed = False
    needs_processing = True

    has_success_history = ProcessingHistory.has_history_for_hash(
        file_hash=file_hash,
        success=True,
    )
    has_failure_history = ProcessingHistory.has_history_for_hash(
        file_hash=file_hash,
        success=False,
    )

    if has_success_history:
        decision = _handle_success_history(ctx, file_hash)
        processed = decision.processed
        needs_processing = decision.needs_processing
        if decision.can_short_circuit:
            assert isinstance(ctx.current_video, VideoFile)
            return ctx.current_video, processed, needs_processing
    elif has_failure_history:
        _handle_failure_history(ctx, file_hash)

    video = _get_or_create_video_instance(
        ctx,
        file_path=file_path,
        file_hash=file_hash,
    )
    if not isinstance(ctx.current_video, VideoFile):
        ctx.current_video = video
    _record_processing_attempt(file_hash, has_success_history=has_success_history)

    logger.info(
        "Video instance ready for processing: pk=%s, file_type=%s (needs_processing=True)",
        video.pk,
        file_type,
    )

    return video, processed, needs_processing

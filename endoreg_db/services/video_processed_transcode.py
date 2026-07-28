from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Never, Protocol, cast
from uuid import uuid4

from django.db import transaction

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.schemas.video_storage import (
    SegmentTimelineReference,
    VideoArtifactProbe,
    VideoFpsResamplingEvidence,
    VideoStorageNormalizationEvidence,
    VideoTimelineContract,
)
from endoreg_db.services.hls_media import materialize_video_hls
from endoreg_db.services.media_operation_gate import defer_if_video_media_busy
from endoreg_db.services.streamable_media import sync_video_streamable_artifacts
from endoreg_db.services.video_files.io import ensure_local_processed_video_file
from endoreg_db.services.video_storage_normalization import (
    assert_temporal_equivalence,
    configured_video_storage_profile,
    evidence_as_json,
    probe_video_artifact,
    persist_video_source_timeline,
    segment_timeline_references,
    timeline_from_video_metadata,
    validate_annotation_fps_resample,
    validate_normalized_output,
)
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.file_operations import (
    ensure_directory,
    ensure_disk_capacity,
    safe_unlink_file,
)
from endoreg_db.utils.hashs import get_video_hash
from endoreg_db.utils.storage import save_local_file
from endoreg_db.utils.transcode_execution import transcode_video

logger = logging.getLogger(__name__)

TranscodeStatus = Literal[
    "changed",
    "dry_run",
    "failed",
    "skipped_missing_processed_file",
    "skipped_not_smaller",
    "skipped_same_hash",
]


class _StorageWithDelete(Protocol):
    def exists(self, name: str) -> bool: ...

    def delete(self, name: str) -> None: ...


class _ProcessedFileWithStorage(Protocol):
    storage: _StorageWithDelete


@dataclass(frozen=True)
class ProcessedVideoTranscodeResult:
    video_id: int
    status: TranscodeStatus
    old_hash: str
    new_hash: str
    old_size: int
    new_size: int
    old_processed_name: str
    new_processed_name: str
    old_streamable_relative_path: str
    new_streamable_relative_path: str
    detail: str = ""

    @property
    def changed(self) -> bool:
        return self.status == "changed"


@dataclass(frozen=True)
class ProcessedVideoTranscodeSummary:
    selected: int = 0
    changed: int = 0
    dry_run: int = 0
    skipped: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "selected": self.selected,
            "changed": self.changed,
            "dry_run": self.dry_run,
            "skipped": self.skipped,
            "failed": self.failed,
        }


@dataclass(frozen=True)
class _OriginalProcessedState:
    processed_name: str
    content_hash: str
    streamable_relative_path: str


@dataclass(frozen=True)
class _TranscodeCandidate:
    path: Path
    old_size: int
    new_size: int
    content_hash: str
    processed_name: str
    output_probe: VideoArtifactProbe
    normalization_evidence: VideoStorageNormalizationEvidence
    fps_resampling_evidence: VideoFpsResamplingEvidence | None


class _TerminalTranscodeResult(Exception):
    def __init__(self, result: ProcessedVideoTranscodeResult) -> None:
        super().__init__(result.detail)
        self.result = result


def _processed_storage_name(*, video: VideoFile, content_hash: str) -> str:
    target_path = path_utils.ANONYM_VIDEO_DIR / f"{video.video_hash}.{content_hash}.mp4"
    return path_utils.to_storage_relative(target_path)


def _managed_streamable_path(relative_path: str) -> Path | None:
    normalized = str(relative_path or "").strip()
    if not normalized:
        return None

    try:
        return path_utils.resolve_protected_media_path(normalized)
    except ValueError:
        pass

    relative = Path(normalized)
    if relative.is_absolute():
        return None

    for storage_root in dict.fromkeys(
        (
            Path(path_utils.STORAGE_DIR).resolve(),
            path_utils.EndoregPathsModel.from_environment().storage.resolve(),
        )
    ):
        candidate = (storage_root / relative).resolve()
        try:
            candidate.relative_to(storage_root)
        except ValueError:
            continue
        return candidate
    return None


def _cleanup_replaced_processed_assets(
    *,
    video_id: int,
    old_processed_name: str,
    new_processed_name: str,
    old_streamable_relative_path: str,
    new_streamable_relative_path: str,
) -> None:
    video = VideoFile.objects.get(pk=video_id)
    if old_processed_name and old_processed_name != new_processed_name:
        processed_file = cast(_ProcessedFileWithStorage, video.processed_file)
        storage = processed_file.storage
        try:
            if storage.exists(old_processed_name):
                storage.delete(old_processed_name)
        except FileNotFoundError:
            pass

    old_streamable_path = _managed_streamable_path(old_streamable_relative_path)
    if (
        old_streamable_path is not None
        and old_streamable_relative_path != new_streamable_relative_path
    ):
        safe_unlink_file(old_streamable_path, missing_ok=True)


def _original_processed_state(video: VideoFile) -> _OriginalProcessedState:
    return _OriginalProcessedState(
        processed_name=str(getattr(video.processed_file, "name", "") or ""),
        content_hash=str(video.processed_video_hash or ""),
        streamable_relative_path=str(video.processed_streamable_relative_path or ""),
    )


def _transcode_result(
    video: VideoFile,
    original: _OriginalProcessedState,
    *,
    status: TranscodeStatus,
    old_size: int = 0,
    new_size: int = 0,
    new_hash: str = "",
    new_processed_name: str = "",
    new_streamable_relative_path: str | None = None,
    detail: str = "",
) -> ProcessedVideoTranscodeResult:
    return ProcessedVideoTranscodeResult(
        video_id=video.pk,
        status=status,
        old_hash=original.content_hash,
        new_hash=new_hash,
        old_size=old_size,
        new_size=new_size,
        old_processed_name=original.processed_name,
        new_processed_name=new_processed_name,
        old_streamable_relative_path=original.streamable_relative_path,
        new_streamable_relative_path=(
            original.streamable_relative_path
            if new_streamable_relative_path is None
            else new_streamable_relative_path
        ),
        detail=detail,
    )


def _stop_transcode(
    video: VideoFile,
    original: _OriginalProcessedState,
    *,
    status: TranscodeStatus,
    old_size: int = 0,
    new_size: int = 0,
    new_hash: str = "",
    new_processed_name: str = "",
    detail: str,
) -> Never:
    raise _TerminalTranscodeResult(
        _transcode_result(
            video,
            original,
            status=status,
            old_size=old_size,
            new_size=new_size,
            new_hash=new_hash,
            new_processed_name=new_processed_name,
            detail=detail,
        )
    )


def _validate_resampling_preconditions(
    video: VideoFile,
    resample_max_fps: float | None,
) -> None:
    if resample_max_fps is None:
        return
    if LabelVideoSegment.objects.filter(video_file=video).exists():
        raise RuntimeError(
            "Annotation FPS resampling must run before segment rows exist."
        )
    if video.frames.filter(is_extracted=True).exists():
        raise RuntimeError(
            "Annotation FPS resampling refuses extracted frame rows because "
            "their coordinates would be invalidated."
        )


def _stored_video_timeline(video: VideoFile) -> VideoTimelineContract:
    stored_fps = video.fps
    stored_duration = video.duration
    stored_frame_count = video.frame_count
    if stored_fps is None or stored_duration is None or stored_frame_count is None:
        raise RuntimeError(
            "Stored FPS, duration, and frame count are required before "
            "normalizing an existing processed video."
        )
    return timeline_from_video_metadata(
        fps=float(stored_fps),
        duration_seconds=float(stored_duration),
        frame_count=int(stored_frame_count),
    )


def _segment_references_for_candidate(
    video: VideoFile,
    *,
    source_probe: VideoArtifactProbe,
    resample_max_fps: float | None,
) -> list[SegmentTimelineReference]:
    if resample_max_fps is not None:
        return []
    profile = configured_video_storage_profile()
    assert_temporal_equivalence(
        _stored_video_timeline(video),
        source_probe.timeline,
        profile=profile,
    )
    return segment_timeline_references(video, timeline=source_probe.timeline)


def _probe_transcoded_candidate(
    video: VideoFile,
    original: _OriginalProcessedState,
    *,
    source_path: Path,
    output_path: Path,
    old_size: int,
    quality_mode: str,
    force_cpu: bool,
    resample_max_fps: float | None,
) -> tuple[Path, int, VideoArtifactProbe]:
    profile = configured_video_storage_profile()
    transcoded_path = transcode_video(
        source_path,
        output_path,
        quality_mode=quality_mode,
        force_cpu=force_cpu,
        extra_args=profile.ffmpeg_output_args(target_fps=resample_max_fps),
    )
    if transcoded_path is None:
        _stop_transcode(
            video,
            original,
            status="failed",
            old_size=old_size,
            detail="ffmpeg transcode failed",
        )
    candidate_path = Path(transcoded_path)
    new_size = candidate_path.stat().st_size
    if new_size <= 0:
        _stop_transcode(
            video,
            original,
            status="failed",
            old_size=old_size,
            new_size=new_size,
            detail="transcoded output is empty",
        )
    return candidate_path, new_size, probe_video_artifact(candidate_path)


def _validate_candidate_output(
    *,
    source_probe: VideoArtifactProbe,
    output_probe: VideoArtifactProbe,
    segment_references: list[SegmentTimelineReference],
    resample_max_fps: float | None,
) -> tuple[VideoStorageNormalizationEvidence, VideoFpsResamplingEvidence | None]:
    profile = configured_video_storage_profile()
    if resample_max_fps is None:
        return (
            validate_normalized_output(
                source=source_probe,
                output=output_probe,
                profile=profile,
                segments=segment_references,
            ),
            None,
        )
    fps_evidence = validate_annotation_fps_resample(
        source=source_probe,
        output=output_probe,
        max_fps=resample_max_fps,
        profile=profile,
    )
    normalization_evidence = validate_normalized_output(
        source=output_probe,
        output=output_probe,
        profile=profile,
    )
    return normalization_evidence, fps_evidence


def _validate_candidate_policy(
    video: VideoFile,
    original: _OriginalProcessedState,
    *,
    candidate_path: Path,
    old_size: int,
    new_size: int,
    allow_larger: bool,
) -> tuple[str, str]:
    if not allow_larger and new_size >= old_size:
        _stop_transcode(
            video,
            original,
            status="skipped_not_smaller",
            old_size=old_size,
            new_size=new_size,
            detail="transcoded output is not smaller",
        )
    new_hash = get_video_hash(candidate_path)
    if new_hash == original.content_hash:
        _stop_transcode(
            video,
            original,
            status="skipped_same_hash",
            old_size=old_size,
            new_size=new_size,
            new_hash=new_hash,
            new_processed_name=original.processed_name,
            detail="transcoded output hash matches existing processed hash",
        )
    if (
        type(video)
        .objects.filter(processed_video_hash=new_hash)
        .exclude(pk=video.pk)
        .exists()
    ):
        _stop_transcode(
            video,
            original,
            status="failed",
            old_size=old_size,
            new_size=new_size,
            new_hash=new_hash,
            detail="processed_video_hash already exists on another video",
        )
    return new_hash, _processed_storage_name(video=video, content_hash=new_hash)


def _build_transcode_candidate(
    video: VideoFile,
    original: _OriginalProcessedState,
    *,
    source_path: Path,
    output_path: Path,
    quality_mode: str,
    force_cpu: bool,
    allow_larger: bool,
    resample_max_fps: float | None,
) -> _TranscodeCandidate:
    old_size = source_path.stat().st_size
    ensure_disk_capacity(destination_dir=output_path.parent, required_bytes=old_size)
    _validate_resampling_preconditions(video, resample_max_fps)
    source_probe = probe_video_artifact(source_path)
    segment_references = _segment_references_for_candidate(
        video,
        source_probe=source_probe,
        resample_max_fps=resample_max_fps,
    )
    candidate_path, new_size, output_probe = _probe_transcoded_candidate(
        video,
        original,
        source_path=source_path,
        output_path=output_path,
        old_size=old_size,
        quality_mode=quality_mode,
        force_cpu=force_cpu,
        resample_max_fps=resample_max_fps,
    )
    normalization_evidence, fps_evidence = _validate_candidate_output(
        source_probe=source_probe,
        output_probe=output_probe,
        segment_references=segment_references,
        resample_max_fps=resample_max_fps,
    )
    new_hash, new_name = _validate_candidate_policy(
        video,
        original,
        candidate_path=candidate_path,
        old_size=old_size,
        new_size=new_size,
        allow_larger=allow_larger,
    )
    return _TranscodeCandidate(
        path=candidate_path,
        old_size=old_size,
        new_size=new_size,
        content_hash=new_hash,
        processed_name=new_name,
        output_probe=output_probe,
        normalization_evidence=normalization_evidence,
        fps_resampling_evidence=fps_evidence,
    )


def _apply_candidate_metadata(
    video: VideoFile,
    candidate: _TranscodeCandidate,
) -> None:
    existing_meta = dict(video.meta or {})
    existing_meta["storage_normalization"] = evidence_as_json(
        candidate.normalization_evidence
    )
    fps_evidence = candidate.fps_resampling_evidence
    if fps_evidence is not None:
        existing_meta["fps_normalization"] = evidence_as_json(fps_evidence)
        video.fps = candidate.output_probe.timeline.fps
        video.duration = candidate.output_probe.timeline.duration_seconds
        video.frame_count = candidate.output_probe.timeline.frame_count
    video.meta = existing_meta


def _reset_frames_after_resampling(
    video: VideoFile,
    candidate: _TranscodeCandidate,
) -> None:
    if candidate.fps_resampling_evidence is None:
        return
    from endoreg_db.services.video_files.frames import initialize_video_frames

    video.frames.all().delete()
    initialize_video_frames(video)
    persist_video_source_timeline(video, candidate.path)


def _publish_transcode_candidate(
    video: VideoFile,
    original: _OriginalProcessedState,
    candidate: _TranscodeCandidate,
) -> str:
    with transaction.atomic():
        save_local_file(
            video.processed_file,
            candidate.path,
            name=candidate.processed_name,
            save=False,
            overwrite=True,
        )
        video.processed_video_hash = candidate.content_hash
        video.processed_streamable_relative_path = ""
        _apply_candidate_metadata(video, candidate)
        video.save(
            update_fields=[
                "processed_file",
                "processed_video_hash",
                "processed_streamable_relative_path",
                "meta",
                "fps",
                "duration",
                "frame_count",
                "date_modified",
            ]
        )
        _reset_frames_after_resampling(video, candidate)
        sync_video_streamable_artifacts(
            video,
            include_raw=False,
            include_processed=True,
            save=True,
        )
        new_streamable_relative_path = str(
            video.processed_streamable_relative_path or ""
        )
        materialize_video_hls(
            int(video.pk),
            artifact_kind="processed",
            force=True,
        )
        transaction.on_commit(
            lambda: _cleanup_replaced_processed_assets(
                video_id=video.pk,
                old_processed_name=original.processed_name,
                new_processed_name=candidate.processed_name,
                old_streamable_relative_path=original.streamable_relative_path,
                new_streamable_relative_path=new_streamable_relative_path,
            )
        )
    return new_streamable_relative_path


def _cleanup_failed_candidate(
    video: VideoFile,
    *,
    original_name: str,
    candidate_name: str,
) -> None:
    if not candidate_name or candidate_name == original_name:
        return
    try:
        processed_file = cast(_ProcessedFileWithStorage, video.processed_file)
        storage = processed_file.storage
        if storage.exists(candidate_name):
            storage.delete(candidate_name)
    except Exception:
        logger.warning(
            "Failed to clean up orphaned transcoded processed file %s",
            candidate_name,
            exc_info=True,
        )


def _dry_run_result(
    video: VideoFile,
    original: _OriginalProcessedState,
    candidate: _TranscodeCandidate,
) -> ProcessedVideoTranscodeResult:
    return _transcode_result(
        video,
        original,
        status="dry_run",
        old_size=candidate.old_size,
        new_size=candidate.new_size,
        new_hash=candidate.content_hash,
        new_processed_name=candidate.processed_name,
        detail="would replace processed file and streamable artifact",
    )


def transcode_processed_video_for_storage_pressure(
    video: VideoFile,
    *,
    apply: bool,
    quality_mode: str = "balanced",
    force_cpu: bool = False,
    allow_larger: bool = False,
    resample_max_fps: float | None = None,
) -> ProcessedVideoTranscodeResult:
    original = _original_processed_state(video)
    if not original.processed_name:
        return _transcode_result(
            video,
            original,
            status="skipped_missing_processed_file",
            detail="processed_file is empty",
        )
    paths = path_utils.EndoregPathsModel.from_environment()
    work_dir = ensure_directory(paths.transcoding / "processed_storage_pressure")
    output_path = (
        work_dir
        / f"video-{video.pk}.{os.getpid()}.{uuid4().hex}.processed.transcoded.mp4"
    )
    saved_new_processed_name = ""
    try:
        defer_if_video_media_busy(video_id=int(video.pk))
        with ensure_local_processed_video_file(video) as source_path:
            candidate = _build_transcode_candidate(
                video,
                original,
                source_path=Path(source_path),
                output_path=output_path,
                quality_mode=quality_mode,
                force_cpu=force_cpu,
                allow_larger=allow_larger,
                resample_max_fps=resample_max_fps,
            )
            if not apply:
                return _dry_run_result(video, original, candidate)
            saved_new_processed_name = candidate.processed_name
            new_streamable_relative_path = _publish_transcode_candidate(
                video,
                original,
                candidate,
            )
            return _transcode_result(
                video,
                original,
                status="changed",
                old_size=candidate.old_size,
                new_size=candidate.new_size,
                new_hash=candidate.content_hash,
                new_processed_name=candidate.processed_name,
                new_streamable_relative_path=new_streamable_relative_path,
            )
    except _TerminalTranscodeResult as terminal:
        return terminal.result
    except Exception as exc:
        logger.exception("Failed to transcode processed video %s", video.pk)
        _cleanup_failed_candidate(
            video,
            original_name=original.processed_name,
            candidate_name=saved_new_processed_name,
        )
        return _transcode_result(
            video,
            original,
            status="failed",
            detail=str(exc),
        )
    finally:
        safe_unlink_file(output_path, missing_ok=True)


def summarize_processed_video_transcode_results(
    results: list[ProcessedVideoTranscodeResult],
) -> ProcessedVideoTranscodeSummary:
    changed = sum(1 for result in results if result.status == "changed")
    dry_run = sum(1 for result in results if result.status == "dry_run")
    failed = sum(1 for result in results if result.status == "failed")
    skipped = len(results) - changed - dry_run - failed
    return ProcessedVideoTranscodeSummary(
        selected=len(results),
        changed=changed,
        dry_run=dry_run,
        skipped=skipped,
        failed=failed,
    )

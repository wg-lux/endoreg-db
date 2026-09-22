from __future__ import annotations

from endoreg_db.utils.storage.files import canonical_media_name

import logging
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Never
from uuid import uuid4

from django.db import transaction
from django.db.models import Q
from django.db.models.fields.files import FieldFile

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
from endoreg_db.services.hls_media import hls_result_is_ready, materialize_video_hls
from endoreg_db.services.media_operation_gate import (
    TranscodeLeaseClaim,
    video_transcode_lease,
    video_transcode_publication,
)
from endoreg_db.services.processed_video_cleanup import (
    cleanup_processed_video_generations,
    commit_processed_replacements,
    record_processed_replacement,
)
from endoreg_db.schemas.processed_video_cleanup import cleanup_receipts
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
from endoreg_db.utils.paths import (
    get_runtime_paths,
    resolve_protected_media_path,
    to_storage_relative,
)
from endoreg_db.utils.file_operations import (
    ensure_directory,
    atomic_create_file,
    ensure_disk_capacity,
    safe_delete_field_file,
    get_file_hash,
    safe_rmtree,
    set_path_mode,
)
from endoreg_db.utils.encryption.storage_materialization import (
    materialized_plaintext_field_file,
)
from endoreg_db.utils.structured_logging import emit_structured_event
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


CleanupPhase = Literal["published_generation", "failed_candidate"]


class ProcessedVideoTranscodeCleanupError(RuntimeError):
    """Cleanup needs reconciliation; never retry by deleting a published master."""

    video_id: int
    phase: CleanupPhase

    def __init__(self, *, video_id: int, phase: CleanupPhase) -> None:
        self.video_id = video_id
        self.phase = phase
        super().__init__(
            f"Processed video cleanup requires reconciliation: video={video_id}, phase={phase}"
        )


def _raise_cleanup_error(
    *, video_id: int, phase: CleanupPhase, cause: Exception
) -> Never:
    emit_structured_event(
        logger,
        "processed_video_transcode_cleanup_failed",
        level=logging.ERROR,
        video_id=video_id,
        phase=phase,
        error_type=type(cause).__name__,
        requires_reconciliation=True,
    )
    raise ProcessedVideoTranscodeCleanupError(video_id=video_id, phase=phase) from cause


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
    published: bool = False
    failure_stage: str = ""

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
    target_path = get_runtime_paths().anonym_video / canonical_media_name(
        video.raw_video_hash, ".mp4", generation=content_hash
    )
    return to_storage_relative(target_path)


def _cleanup_committed_processed_assets(*, video_id: int) -> None:
    try:
        result = cleanup_processed_video_generations(video_id, apply=True)
        if result.pending:
            raise RuntimeError(f"Processed cleanup is pending: {result.reason}")
    except Exception as exc:
        _raise_cleanup_error(video_id=video_id, phase="published_generation", cause=exc)


def _delete_unreferenced_processed_file(video: VideoFile, name: str) -> None:
    # In-memory FieldFile state can reflect a rolled-back publication. Consult the
    # database before deleting; an unavailable database must retain the artifact.
    with transaction.atomic():
        VideoFile.objects.select_for_update().get(pk=video.pk)
        if VideoFile.objects.filter(Q(processed_file=name) | Q(raw_file=name)).exists():
            raise RuntimeError("Refusing to delete a referenced processed artifact")
        safe_delete_field_file(FieldFile(video, video.processed_file.field, name))


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
    published: bool = False,
    failure_stage: str = "",
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
        published=published,
        failure_stage=failure_stage,
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
    if candidate_path != output_path:
        raise RuntimeError("Encoder returned a path outside its scoped output.")
    set_path_mode(candidate_path, 0o600)
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
    new_hash = get_file_hash(candidate_path)
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
    *,
    claim: TranscodeLeaseClaim,
) -> str:
    # Encryption/upload has already finished. Hold the publication row lock only
    # while checking the source generation and swapping database references.
    with video_transcode_publication(claim) as current:
        if (
            str(current.processed_file.name or "") != original.processed_name
            or str(current.processed_video_hash or "") != original.content_hash
        ):
            raise RuntimeError("Processed source generation changed during transcode.")
        current.processed_file.name = candidate.processed_name
        current.processed_video_hash = candidate.content_hash
        _apply_candidate_metadata(current, candidate)
        record_processed_replacement(
            current,
            previous_name=original.processed_name,
            previous_hash=original.content_hash,
            strict=True,
        )
        _record_legacy_playback_retirement(current)
        if not commit_processed_replacements(current):
            raise RuntimeError("Replacement publication requires a cleanup receipt.")
        current.save(
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
        _reset_frames_after_resampling(current, candidate)
    video.refresh_from_db()
    return str(video.processed_streamable_relative_path or "")


def _stage_encrypted_candidate(
    video: VideoFile,
    candidate: _TranscodeCandidate,
) -> _TranscodeCandidate:
    target = video.processed_file
    name = to_storage_relative(
        get_runtime_paths().anonym_video
        / ".generations"
        / canonical_media_name(video.raw_video_hash, ".mp4", generation=uuid4().hex)
    )
    staged = FieldFile(video, target.field, "")
    stored_name = save_local_file(staged, candidate.path, name=name, save=False)
    # Confirm authenticated round-trip identity before database publication.
    try:
        if get_file_hash(staged) != candidate.content_hash:
            raise RuntimeError("Encrypted candidate identity verification failed.")
    except Exception:
        try:
            safe_delete_field_file(staged)
        except Exception as cleanup_error:
            _raise_cleanup_error(
                video_id=int(video.pk), phase="failed_candidate", cause=cleanup_error
            )
        raise
    return replace(candidate, processed_name=stored_name)


def _cleanup_failed_candidate(
    video: VideoFile,
    *,
    original_name: str,
    candidate_name: str,
) -> None:
    if not candidate_name or candidate_name == original_name:
        return
    try:
        _delete_unreferenced_processed_file(video, candidate_name)
    except Exception as exc:
        _raise_cleanup_error(
            video_id=int(video.pk), phase="failed_candidate", cause=exc
        )


def _prepare_existing_generations(video: VideoFile) -> None:
    """Retire journaled replacements before permitting another generation."""
    if cleanup_receipts(video.meta):
        result = materialize_video_hls(int(video.pk), artifact_kind="processed")
        if not hls_result_is_ready(result.status):
            raise RuntimeError("Pending replacement playback is not ready.")
        _cleanup_committed_processed_assets(video_id=video.pk)
        video.refresh_from_db()


def _record_legacy_playback_retirement(video: VideoFile) -> None:
    if video.processed_streamable_relative_path:
        legacy_path = resolve_protected_media_path(
            video.processed_streamable_relative_path
        )
        record_processed_replacement(
            video,
            previous_name=video.processed_streamable_relative_path,
            previous_hash=get_file_hash(legacy_path),
            source_kind="legacy_streamable",
            strict=True,
        )
    video.processed_streamable_relative_path = ""


def _retire_existing_playback(video: VideoFile, *, claim: TranscodeLeaseClaim) -> None:
    if not video.processed_streamable_relative_path:
        return
    with video_transcode_publication(claim) as current:
        _record_legacy_playback_retirement(current)
        if not commit_processed_replacements(current):
            raise RuntimeError("Legacy playback retirement requires a cleanup receipt.")
        current.save(
            update_fields=[
                "processed_streamable_relative_path",
                "meta",
                "date_modified",
            ]
        )
    video.refresh_from_db()
    result = materialize_video_hls(int(video.pk), artifact_kind="processed", force=True)
    if not hls_result_is_ready(result.status):
        raise RuntimeError(
            "Canonical playback is not ready for legacy-copy retirement."
        )
    _cleanup_committed_processed_assets(video_id=video.pk)
    video.refresh_from_db()


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
    transcode_claim: TranscodeLeaseClaim | None = None,
    expected_processed_name: str | None = None,
    expected_processed_hash: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> ProcessedVideoTranscodeResult:
    with video_transcode_lease(video_id=int(video.pk), claim=transcode_claim) as claim:
        video.refresh_from_db()
        return _transcode_under_lease(
            video,
            apply=apply,
            quality_mode=quality_mode,
            force_cpu=force_cpu,
            allow_larger=allow_larger,
            resample_max_fps=resample_max_fps,
            claim=claim,
            expected_processed_name=expected_processed_name,
            expected_processed_hash=expected_processed_hash,
            progress_callback=progress_callback,
        )


@contextmanager
def ensure_local_processed_video_file(
    video: VideoFile,
    *,
    directory: Path | None = None,
) -> Generator[Path]:
    """Authenticated source materialization; never return a ciphertext path."""
    with materialized_plaintext_field_file(
        video.processed_file,
        suffix=".mp4",
        prefix="processed-transcode-source-",
        directory=directory,
    ) as source_path:
        yield source_path


def _transcode_under_lease(
    video: VideoFile,
    *,
    apply: bool,
    quality_mode: str,
    force_cpu: bool,
    allow_larger: bool,
    resample_max_fps: float | None,
    claim: TranscodeLeaseClaim,
    expected_processed_name: str | None,
    expected_processed_hash: str | None,
    progress_callback: Callable[[str], None] | None,
) -> ProcessedVideoTranscodeResult:
    original = _original_processed_state(video)
    if (
        expected_processed_name is not None
        and expected_processed_name != original.processed_name
        or expected_processed_hash is not None
        and expected_processed_hash != original.content_hash
    ):
        raise RuntimeError("Queued processed source generation no longer matches.")
    if not original.processed_name:
        return _transcode_result(
            video,
            original,
            status="skipped_missing_processed_file",
            detail="processed_file is empty",
        )
    attempt_root = (
        get_runtime_paths().transcoding / "processed_storage_pressure" / str(video.uuid)
    )
    if attempt_root.exists():
        raise RuntimeError(
            "Previous transcode staging requires cleanup reconciliation."
        )
    ensure_directory(attempt_root, dir_mode=0o700)
    work_dir = ensure_directory(
        attempt_root / uuid4().hex,
        dir_mode=0o700,
    )
    output_path = work_dir / "candidate.mp4"
    saved_new_processed_name = ""
    published = False
    cleanup_complete = True
    stage = "materializing"
    candidate: _TranscodeCandidate | None = None

    def progress(value: str) -> None:
        nonlocal stage
        stage = value
        if progress_callback is not None:
            progress_callback(value)

    try:
        if apply:
            progress("cleanup")
            _prepare_existing_generations(video)
            original = _original_processed_state(video)
        progress("materializing")
        atomic_create_file(destination=output_path, content=(), file_mode=0o600)
        with ensure_local_processed_video_file(
            video, directory=work_dir
        ) as source_path:
            if get_file_hash(source_path) != original.content_hash:
                raise RuntimeError(
                    "Processed source content identity does not match its generation."
                )
            progress("transcoding")
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
        # Source plaintext is gone before encryption or publication begins.
        progress("validating")
        if not apply:
            return _dry_run_result(video, original, candidate)
        candidate = _stage_encrypted_candidate(video, candidate)
        saved_new_processed_name = candidate.processed_name
        progress("publishing")
        new_streamable_relative_path = _publish_transcode_candidate(
            video,
            original,
            candidate,
            claim=claim,
        )
        published = True
        progress("rebuilding_playback")
        hls_result = materialize_video_hls(
            int(video.pk), artifact_kind="processed", force=True
        )
        if not hls_result_is_ready(hls_result.status):
            raise RuntimeError("Published video playback generation is not ready.")
        progress("cleanup")
        _cleanup_committed_processed_assets(video_id=video.pk)
        return _transcode_result(
            video,
            original,
            status="changed",
            old_size=candidate.old_size,
            new_size=candidate.new_size,
            new_hash=candidate.content_hash,
            new_processed_name=candidate.processed_name,
            new_streamable_relative_path=new_streamable_relative_path,
            published=True,
        )
    except _TerminalTranscodeResult as terminal:
        if apply and terminal.result.status in {
            "skipped_not_smaller",
            "skipped_same_hash",
        }:
            _retire_existing_playback(video, claim=claim)
        return terminal.result
    except ProcessedVideoTranscodeCleanupError:
        cleanup_complete = bool(published or cleanup_receipts(video.meta))
        # Cleanup failures must never enter candidate rollback cleanup.
        raise
    except Exception as exc:
        emit_structured_event(
            logger,
            "processed_video_transcode_failed",
            level=logging.ERROR,
            video_id=int(video.pk),
            error_type=type(exc).__name__,
            stage=stage,
            published=published,
        )
        if not published:
            try:
                _cleanup_failed_candidate(
                    video,
                    original_name=original.processed_name,
                    candidate_name=saved_new_processed_name,
                )
            except ProcessedVideoTranscodeCleanupError:
                cleanup_complete = False
                raise
        return _transcode_result(
            video,
            original,
            status="failed",
            published=published,
            failure_stage=stage,
            old_size=candidate.old_size if candidate else 0,
            new_size=candidate.new_size if candidate else 0,
            new_hash=candidate.content_hash if candidate else "",
            new_processed_name=saved_new_processed_name if published else "",
            detail=f"Transcode failed during {stage}; {type(exc).__name__}.",
        )
    finally:
        try:
            safe_rmtree(work_dir, missing_ok=True)
            if cleanup_complete:
                safe_rmtree(attempt_root, missing_ok=True)
        except Exception as exc:
            _raise_cleanup_error(
                video_id=int(video.pk),
                phase="published_generation" if published else "failed_candidate",
                cause=exc,
            )


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

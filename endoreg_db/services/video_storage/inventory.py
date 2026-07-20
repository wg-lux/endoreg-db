from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from endoreg_db.schemas.video_storage import (
    ClinicalFrameQualityEvidence,
    VideoSourceTimelineEvidence,
    VideoStorageNormalizationEvidence,
)
from endoreg_db.services.video_storage.contracts import (
    VideoStorageInventoryReport,
    VideoStorageNormalizationError,
)
from endoreg_db.services.video_storage.timelines import segment_timeline_references

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


class _StoragePathProvider(Protocol):
    def path(self, name: str) -> str: ...


class _StorageExistsProvider(Protocol):
    def exists(self, name: str) -> bool: ...


def _field_file_size(field_file: object) -> int:
    name = str(getattr(field_file, "name", "") or "")
    if not name:
        return 0
    storage = getattr(field_file, "storage", None)
    if storage is not None:
        storage_path = getattr(storage, "path", None)
        if callable(storage_path):
            try:
                provider = cast(_StoragePathProvider, storage)
                candidate = Path(provider.path(name))
                if candidate.is_file():
                    return int(candidate.stat().st_size)
            except (OSError, TypeError, ValueError):
                pass
    try:
        size = int(getattr(field_file, "size"))
    except (AttributeError, OSError, TypeError, ValueError):
        return 0
    return max(0, size)


def _field_file_reference_state(field_file: object) -> tuple[int, int]:
    name = str(getattr(field_file, "name", "") or "")
    if not name:
        return (0, 0)
    storage = getattr(field_file, "storage", None)
    storage_exists = getattr(storage, "exists", None)
    if callable(storage_exists):
        try:
            provider = cast(_StorageExistsProvider, storage)
            return (1, 0 if provider.exists(name) else 1)
        except (OSError, TypeError, ValueError):
            return (1, 1)
    return (1, 0 if _field_file_size(field_file) > 0 else 1)


def _protected_relative_file_size(relative_path: str) -> int:
    if not relative_path.strip():
        return 0
    from endoreg_db.utils.paths import resolve_existing_protected_media_path

    candidate = resolve_existing_protected_media_path(relative_path)
    if candidate is None or not candidate.is_file():
        return 0
    return int(candidate.stat().st_size)


def _protected_relative_reference_state(relative_path: str) -> tuple[int, int]:
    if not relative_path.strip():
        return (0, 0)
    from endoreg_db.utils.paths import resolve_existing_protected_media_path

    candidate = resolve_existing_protected_media_path(relative_path)
    return (1, 0 if candidate is not None and candidate.is_file() else 1)


def _hls_reference_state(artifact: object) -> tuple[int, int]:
    from endoreg_db.utils.paths import resolve_existing_protected_media_path

    referenced = 0
    missing = 0
    playlist_relative = str(getattr(artifact, "playlist_relative_path", "") or "")
    if playlist_relative:
        referenced += 1
        playlist = resolve_existing_protected_media_path(playlist_relative)
        if playlist is None or not playlist.is_file():
            missing += 1
    segment_relative = str(
        getattr(artifact, "segment_directory_relative_path", "") or ""
    )
    if segment_relative:
        referenced += 1
        segment_dir = resolve_existing_protected_media_path(segment_relative)
        if segment_dir is None or not segment_dir.is_dir():
            missing += 1
    return (referenced, missing)


def _hls_artifact_size(artifact: object) -> int:
    from endoreg_db.utils.paths import resolve_existing_protected_media_path

    paths: set[Path] = set()
    playlist_relative = str(getattr(artifact, "playlist_relative_path", "") or "")
    playlist = resolve_existing_protected_media_path(playlist_relative)
    if playlist is not None and playlist.is_file():
        paths.add(playlist.resolve())

    segment_relative = str(
        getattr(artifact, "segment_directory_relative_path", "") or ""
    )
    segment_dir = resolve_existing_protected_media_path(segment_relative)
    if segment_dir is not None and segment_dir.is_dir():
        for candidate in segment_dir.rglob("*"):
            if candidate.is_file() and not candidate.is_symlink():
                paths.add(candidate.resolve())
    return sum(path.stat().st_size for path in paths)


def inventory_video_storage(video: "VideoFile") -> VideoStorageInventoryReport:
    from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact

    raw_hls_bytes = 0
    processed_hls_bytes = 0
    referenced_artifacts = 0
    missing_referenced_artifacts = 0
    for field_file in (video.raw_file, video.processed_file):
        referenced, missing = _field_file_reference_state(field_file)
        referenced_artifacts += referenced
        missing_referenced_artifacts += missing
    for relative_path in (
        str(video.raw_streamable_relative_path or ""),
        str(video.processed_streamable_relative_path or ""),
    ):
        referenced, missing = _protected_relative_reference_state(relative_path)
        referenced_artifacts += referenced
        missing_referenced_artifacts += missing

    for artifact in VideoHlsArtifact.objects.filter(video_id=int(video.pk)):
        size = _hls_artifact_size(artifact)
        referenced, missing = _hls_reference_state(artifact)
        referenced_artifacts += referenced
        missing_referenced_artifacts += missing
        kind = str(getattr(artifact, "artifact_kind", ""))
        if kind == "raw":
            raw_hls_bytes += size
        elif kind == "processed":
            processed_hls_bytes += size

    normalization_verified = video_normalization_evidence(video) is not None
    cleanup_ready = not raw_cleanup_blockers(video)
    state = video.state
    return VideoStorageInventoryReport(
        video_id=int(video.pk),
        raw_bytes=_field_file_size(video.raw_file),
        processed_bytes=_field_file_size(video.processed_file),
        raw_streamable_bytes=_protected_relative_file_size(
            str(video.raw_streamable_relative_path or "")
        ),
        processed_streamable_bytes=_protected_relative_file_size(
            str(video.processed_streamable_relative_path or "")
        ),
        raw_hls_bytes=raw_hls_bytes,
        processed_hls_bytes=processed_hls_bytes,
        anonymization_validated=bool(
            state is not None and state.anonymization_validated
        ),
        normalization_verified=normalization_verified,
        raw_cleanup_ready=cleanup_ready,
        referenced_artifacts=referenced_artifacts,
        missing_referenced_artifacts=missing_referenced_artifacts,
    )


def video_normalization_evidence(
    video: object,
) -> VideoStorageNormalizationEvidence | None:
    meta = getattr(video, "meta", None)
    if not isinstance(meta, dict):
        return None
    typed_meta = cast(dict[str, object], meta)
    raw_evidence = typed_meta.get("storage_normalization")
    if not isinstance(raw_evidence, dict):
        return None
    try:
        return VideoStorageNormalizationEvidence.model_validate(raw_evidence)
    except ValueError:
        return None


def raw_cleanup_blockers(video: "VideoFile") -> list[str]:
    """Return every unmet fail-closed prerequisite for destructive raw cleanup."""
    blockers: list[str] = []
    from endoreg_db.services.media_operation_gate import (
        video_has_active_media_operation_leases,
    )

    if video_has_active_media_operation_leases(int(video.pk)):
        blockers.append("active_media_operation_lease")
    normalization = video_normalization_evidence(video)
    if normalization is None:
        blockers.append("storage_normalization_evidence_missing")
    meta = video.meta if isinstance(video.meta, dict) else {}
    raw_timeline = cast(dict[str, object], meta).get("source_timeline")
    try:
        source_timeline = VideoSourceTimelineEvidence.model_validate(raw_timeline)
    except ValueError:
        source_timeline = None
        blockers.append("source_timeline_evidence_missing")
    raw_quality = cast(dict[str, object], meta).get("clinical_frame_quality")
    try:
        quality = ClinicalFrameQualityEvidence.model_validate(raw_quality)
    except ValueError:
        quality = None
        blockers.append("clinical_frame_quality_approval_missing")
    if (
        quality is not None
        and normalization is not None
        and quality.profile_name != normalization.profile_name
    ):
        blockers.append("clinical_frame_quality_profile_mismatch")

    from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
    from endoreg_db.services.hls_media import get_ready_hls_artifact

    try:
        processed_hls = get_ready_hls_artifact(video=video, artifact_kind="processed")
    except (FileNotFoundError, ValueError, VideoHlsArtifact.DoesNotExist):
        processed_hls = None
    if processed_hls is None:
        blockers.append("processed_hls_not_ready")
    elif processed_hls.source_file_name != str(video.processed_file.name or ""):
        blockers.append("processed_hls_generation_mismatch")

    if source_timeline is not None:
        try:
            segment_timeline_references(
                video,
                timeline=source_timeline.source.timeline,
            )
        except VideoStorageNormalizationError:
            blockers.append("segment_timeline_evidence_invalid")
    return blockers

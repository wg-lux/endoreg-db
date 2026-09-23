"""Retire recorded processed generations after their replacement commits.

The video row lock is shared with playback-lease acquisition and publication.
Receipts survive failed filesystem operations so missing files are retryable.
"""

import json
import logging
import re
from functools import partial
from pathlib import Path
from typing import Literal

from django.db import transaction
from django.db.models import Q
from django.db.models.fields.files import FieldFile
from pydantic import ValidationError

from endoreg_db.models.hub.storage_placement import StorageArtifactPlacement
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.schemas.processed_video_cleanup import (
    ProcessedGenerationCleanupReceipt,
    ProcessedGenerationCleanupResult,
    cleanup_receipts,
)
from endoreg_db.schemas.video_storage import VideoStorageNormalizationEvidence
from endoreg_db.utils.file_operations import get_file_hash
from endoreg_db.utils.file_operations import (
    safe_delete_field_file,
    safe_rmtree,
    safe_unlink_file,
)
from endoreg_db.utils.paths import get_runtime_paths, to_protected_media_relative

logger = logging.getLogger(__name__)


def record_processed_replacement(
    video: VideoFile,
    *,
    previous_name: str,
    previous_hash: str | None,
    source_kind: Literal["processed", "legacy_streamable"] = "processed",
    strict: bool = False,
) -> None:
    """Record exact identities before HLS publication can retire the old cache."""
    if not previous_name:
        if strict:
            raise ValueError("Previous artifact identity is required")
        return
    if not previous_hash or re.fullmatch(r"[0-9a-f]{64}", previous_hash) is None:
        if strict:
            raise ValueError("Previous artifact digest is required")
        logger.warning(
            json.dumps(
                {
                    "event": "processed_cleanup_deferred",
                    "video_id": video.pk,
                    "reason": "previous_digest_unknown",
                }
            )
        )
        return
    try:
        receipt = ProcessedGenerationCleanupReceipt(
            source_name=previous_name,
            source_kind=source_kind,
            source_sha256=previous_hash,
            replacement_name=str(video.processed_file.name or ""),
            replacement_sha256=video.processed_video_hash or "",
        )
    except ValidationError:
        if strict:
            raise
        logger.warning(
            json.dumps(
                {
                    "event": "processed_cleanup_deferred",
                    "video_id": video.pk,
                    "reason": "generation_identity_unknown",
                }
            )
        )
        return
    receipts = cleanup_receipts(video.meta)
    receipts.append(receipt)
    video.meta = {
        **(video.meta or {}),
        "processed_generation_cleanup": [
            item.model_dump(mode="json") for item in receipts
        ],
    }


def commit_processed_replacements(video: VideoFile) -> bool:
    """Call inside the transaction that records successful import publication."""
    receipts = cleanup_receipts(video.meta)
    if not receipts:
        return False
    committed = [
        ProcessedGenerationCleanupReceipt(
            receipt_id=item.receipt_id,
            source_name=item.source_name,
            source_kind=item.source_kind,
            source_sha256=item.source_sha256,
            replacement_name=str(video.processed_file.name or ""),
            replacement_sha256=video.processed_video_hash or "",
            committed=True,
        )
        for item in receipts
    ]
    video.meta = {
        **(video.meta or {}),
        "processed_generation_cleanup": [
            item.model_dump(mode="json") for item in committed
        ],
    }
    return True


def schedule_processed_generation_cleanup(video_id: int) -> None:
    transaction.on_commit(partial(_cleanup_after_commit, video_id))


def _cleanup_after_commit(video_id: int) -> None:
    # Publication already succeeded. Cleanup failures must not make the importer
    # restore its previous master; the durable receipt is the retry authority.
    try:
        result = cleanup_processed_video_generations(video_id, apply=True)
    except Exception as exc:
        logger.error(
            json.dumps(
                {
                    "event": "processed_cleanup_failed",
                    "video_id": video_id,
                    "error_type": type(exc).__name__,
                }
            )
        )
    else:
        logger.info(json.dumps({"event": "processed_cleanup", **result.model_dump()}))


def _owned_path(path: Path, root: Path) -> Path:
    """Reject aliases and symlinks, including ancestors of a missing retry target."""
    path.relative_to(root)
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise ValueError("Cleanup path has a symbolic-link alias")
        if ancestor == root:
            break
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Cleanup path escapes its owner root")
    return path


def _old_master(
    video: VideoFile, receipt: ProcessedGenerationCleanupReceipt
) -> FieldFile | Path:
    if receipt.source_kind == "legacy_streamable":
        return _old_streamable(video, receipt)
    field = FieldFile(video, video.processed_file.field, receipt.source_name)
    root = get_runtime_paths().anonym_video

    path = _owned_path(Path(field.path), root)

    relative = path.relative_to(root).as_posix()
    identity = re.escape(str(video.raw_video_hash))
    pattern = rf"(?:{identity}\.mp4|\.generations/{identity}-[0-9a-f]{{32}}\.mp4|(?:\.generations/)?{identity}\.(?:[0-9a-f]{{32}}|[0-9a-f]{{64}})\.mp4|[0-9a-f]{{64}}\.[0-9a-f]{{32}}\.mp4)"
    if re.fullmatch(pattern, relative) is None:
        raise ValueError("Previous file is not an owned generated processed master")
    if (
        field.storage.exists(receipt.source_name)
        and get_file_hash(field) != receipt.source_sha256
    ):
        raise ValueError("Previous master digest differs from the cleanup receipt")
    return field


def _old_streamable(
    video: VideoFile, receipt: ProcessedGenerationCleanupReceipt
) -> Path:
    root = get_runtime_paths().streamable_videos_processed_media

    path = _owned_path(root / Path(receipt.source_name).name, root)

    if (
        to_protected_media_relative(path) != receipt.source_name
        or re.fullmatch(
            rf"{re.escape(str(video.raw_video_hash))}(?:\.[a-zA-Z0-9_-]+)?\.mp4",
            path.name,
        )
        is None
    ):
        raise ValueError("Legacy playback identity is not owned by this video")
    if path.exists() and get_file_hash(path) != receipt.source_sha256:
        raise ValueError("Legacy playback digest differs from its cleanup receipt")
    return path


def _old_hls_paths(video: VideoFile, artifacts: list[VideoHlsArtifact]) -> list[Path]:
    root = get_runtime_paths().streamable_videos_processed_media / "hls"
    paths: list[Path] = []
    for artifact in artifacts:
        directory = _owned_path(
            root / str(video.uuid) / str(artifact.key_id) / "v0", root
        )
        if artifact.segment_directory_relative_path != to_protected_media_relative(
            directory
        ) or artifact.playlist_relative_path != to_protected_media_relative(
            directory / "playlist.m3u8"
        ):
            raise ValueError("Superseded HLS paths do not match their generation owner")
        if (
            VideoHlsArtifact.objects.exclude(pk=artifact.pk)
            .filter(
                Q(
                    segment_directory_relative_path=artifact.segment_directory_relative_path
                )
                | Q(playlist_relative_path=artifact.playlist_relative_path)
            )
            .exists()
        ):
            raise ValueError("Superseded HLS paths are shared")
        if directory.exists() and any(
            child.is_symlink() for child in directory.rglob("*")
        ):
            raise ValueError("Superseded HLS contains symbolic links")
        paths.append(directory)
    return paths


def _referenced(receipt: ProcessedGenerationCleanupReceipt) -> bool:
    return (
        VideoFile.objects.filter(
            Q(processed_file=receipt.source_name)
            | Q(raw_file=receipt.source_name)
            | Q(processed_streamable_relative_path=receipt.source_name)
            | Q(raw_streamable_relative_path=receipt.source_name)
        ).exists()
        or UploadJob.objects.filter(file=receipt.source_name).exists()
        or StorageArtifactPlacement.objects.filter(
            sha256=receipt.source_sha256
        ).exists()
    )


def cleanup_processed_video_generations(
    video_id: int, *, apply: bool = False
) -> ProcessedGenerationCleanupResult:
    """Commit writer ownership before the transaction performs slow storage IO."""
    from endoreg_db.exceptions import MediaOperationDeferred
    from endoreg_db.services.media_operation_gate import video_artifact_mutation

    receipts = cleanup_receipts(VideoFile.objects.only("meta").get(pk=video_id).meta)
    if not receipts:
        return ProcessedGenerationCleanupResult(
            video_id=video_id, reason="nothing_pending"
        )
    admitted = False
    try:
        with video_artifact_mutation(video_id=video_id):
            admitted = True
            return _cleanup_processed_video_generations_owned(video_id, apply=apply)
    except MediaOperationDeferred:
        if admitted:
            raise
        return ProcessedGenerationCleanupResult(
            video_id=video_id, pending=len(receipts), reason="active_media_lease"
        )


@transaction.atomic
def _cleanup_processed_video_generations_owned(
    video_id: int, *, apply: bool
) -> ProcessedGenerationCleanupResult:
    """Clean only journaled generations with a verified, committed replacement."""
    video = VideoFile.objects.select_for_update().get(pk=video_id)
    receipts = cleanup_receipts(video.meta)
    result = ProcessedGenerationCleanupResult(
        video_id=video_id, pending=len(receipts), reason="nothing_pending"
    )
    if not receipts:
        return result
    result.reason = "replacement_not_committed"
    if any(
        not item.committed
        or item.replacement_name != video.processed_file.name
        or item.replacement_sha256 != video.processed_video_hash
        for item in receipts
    ):
        return result
    result.reason = "active_media_lease"
    from endoreg_db.services.media_operation_gate import (
        video_has_active_media_operation_leases,
    )

    if video_has_active_media_operation_leases(video_id):
        return result
    # Database blockers are cheap; do not decrypt/hash a large replacement when
    # a referenced source already makes this cleanup attempt ineligible.
    result.reason = "referenced"
    if any(_referenced(receipt) for receipt in receipts):
        return result
    result.reason = "replacement_not_ready"
    evidence = VideoStorageNormalizationEvidence.model_validate(
        (video.meta or {}).get("storage_normalization")
    )
    if not evidence.temporal_equivalent or not evidence.storage_compliant:
        return result
    from endoreg_db.services.hls_media import get_ready_hls_artifact

    try:
        ready = get_ready_hls_artifact(video=video, artifact_kind="processed")
        digest = get_file_hash(video.processed_file)
    except (FileNotFoundError, VideoHlsArtifact.DoesNotExist):
        return result
    if (
        digest != video.processed_video_hash
        or ready.source_content_hash != digest
        or ready.source_file_name != video.processed_file.name
    ):
        return result

    plans: list[
        tuple[
            ProcessedGenerationCleanupReceipt,
            FieldFile | Path,
            list[VideoHlsArtifact],
            list[Path],
        ]
    ] = []
    for receipt in receipts:
        result.reason = "referenced"
        # Recheck after validation in case another reference appeared meanwhile.
        if _referenced(receipt):
            return result
        references = VideoHlsArtifact.objects.filter(
            source_file_name=receipt.source_name
        )
        owned = references.filter(
            video=video,
            artifact_kind="processed",
            status="superseded",
            source_content_hash=receipt.source_sha256,
        )
        if references.exclude(pk__in=owned.values("pk")).exists():
            return result
        artifacts = list(owned.select_for_update())
        plans.append(
            (
                receipt,
                _old_master(video, receipt),
                artifacts,
                _old_hls_paths(video, artifacts),
            )
        )
    result.reason = "dry_run"
    if not apply:
        return result
    for receipt, field, artifacts, paths in plans:
        for path in paths:
            safe_rmtree(path, missing_ok=True)
            if path.exists():
                raise OSError("Superseded HLS deletion did not remove its directory")
        if isinstance(field, Path):
            safe_unlink_file(field, missing_ok=True)
            if field.exists():
                raise OSError("Legacy playback deletion did not remove its file")
        else:
            safe_delete_field_file(field, missing_ok=True)
            if field.storage.exists(receipt.source_name):
                raise OSError(
                    "Superseded master deletion did not remove its storage object"
                )
        VideoHlsArtifact.objects.filter(
            pk__in=[artifact.pk for artifact in artifacts]
        ).delete()
    video.meta = {**(video.meta or {}), "processed_generation_cleanup": []}
    video.save(update_fields=["meta", "date_modified"])
    result.cleaned = len(plans)
    result.pending = 0
    result.reason = "cleaned"
    return result


def reconcile_previous_processed_cleanup(instance: VideoFile) -> None:
    """Retire a previous committed replacement before allocating another master."""
    result = cleanup_processed_video_generations(int(instance.pk), apply=True)
    if result.pending:
        raise RuntimeError(
            "Video publication cannot proceed while previous processed generation "
            f"cleanup is pending: {result.reason}."
        )
    persisted = VideoFile.objects.only(
        "meta", "processed_file", "processed_video_hash"
    ).get(pk=instance.pk)
    # The import context can contain newly extracted clinical metadata. Refresh
    # only publication identities and the authoritative cleanup journal.
    instance.meta = {
        **(instance.meta or {}),
        "processed_generation_cleanup": (persisted.meta or {}).get(
            "processed_generation_cleanup", []
        ),
    }
    instance.processed_file.name = persisted.processed_file.name
    instance.processed_video_hash = persisted.processed_video_hash

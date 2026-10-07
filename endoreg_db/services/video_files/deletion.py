# pyright: reportPrivateUsage=false
"""Explicit video deletion using persisted ownership, never filename searches."""

from pathlib import Path

from django.db.models import Q
from django.db.models.fields.files import FieldFile

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.hub.storage_placement import StorageArtifactPlacement
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.media.source_deletion import (
    check_original,
    recorded_upload_sources,
    complete_source_deletion,
)
from endoreg_db.schemas.processed_video_cleanup import cleanup_receipts
from endoreg_db.services.video_storage.generation_cleanup import (
    _old_master,
    _referenced,
)
from endoreg_db.utils.file_operations import safe_delete_field_file, safe_unlink_file


def delete_recorded_video_copies(video: VideoFile) -> None:
    """Run under the video row lock before deleting its canonical files.

    Explicit deletion may remove failed import sources. Active jobs and shared
    identities still block it. Database ownership survives a filesystem failure.
    """
    from endoreg_db.services.media.operation_gate import defer_if_video_media_busy

    defer_if_video_media_busy(video_id=int(video.pk))
    jobs = list(
        UploadJob.objects.select_for_update()
        .filter(
            content_hash=video.raw_video_hash,
            source_center_id=video.center_id,
            content_type__startswith="video/",
        )
        .order_by("pk")
    )
    if (
        jobs
        and VideoFile.objects.exclude(pk=video.pk)
        .filter(raw_video_hash=video.raw_video_hash)
        .exists()
    ):
        raise ValueError("Original video source is shared with another video")
    if StorageArtifactPlacement.objects.filter(
        sha256__in=[
            value
            for value in (video.raw_video_hash, video.processed_video_hash)
            if value
        ]
    ).exists():
        raise ValueError("Video content still has a storage placement")
    for name in (
        video.raw_file.name,
        video.processed_file.name,
        video.raw_streamable_relative_path,
        video.processed_streamable_relative_path,
    ):
        if name and (
            VideoFile.objects.exclude(pk=video.pk)
            .filter(
                Q(raw_file=name)
                | Q(processed_file=name)
                | Q(raw_streamable_relative_path=name)
                | Q(processed_streamable_relative_path=name)
            )
            .exists()
            or UploadJob.objects.filter(file=name).exists()
            or VideoHlsArtifact.objects.exclude(video=video)
            .filter(source_file_name=name)
            .exists()
        ):
            raise ValueError("Canonical video file is shared")

    originals = recorded_upload_sources(
        jobs, digest=video.raw_video_hash, video_id=int(video.pk)
    )

    generations: dict[str, FieldFile | Path] = {}
    for receipt in cleanup_receipts(video.meta):
        # A stale receipt may also identify an intermediate replacement.
        candidates = [
            receipt,
            receipt.model_copy(
                update={
                    "source_name": receipt.replacement_name,
                    "source_sha256": receipt.replacement_sha256,
                    "source_kind": "processed",
                }
            ),
        ]
        for candidate in candidates:
            if candidate.source_name in {
                video.raw_file.name,
                video.processed_file.name,
            }:
                continue
            if (
                _referenced(candidate)
                or VideoHlsArtifact.objects.exclude(video=video)
                .filter(source_file_name=candidate.source_name)
                .exists()
            ):
                raise ValueError("Recorded video generation is still referenced")
            generations[candidate.source_name] = _old_master(video, candidate)

    # Validate every known source before the first irreversible operation.
    for source in originals:
        check_original(source, video.raw_video_hash)
        safe_unlink_file(source, missing_ok=True)
    for generation in generations.values():
        if isinstance(generation, Path):
            safe_unlink_file(generation, missing_ok=True)
        else:
            safe_delete_field_file(generation, missing_ok=True)
    complete_source_deletion(jobs)

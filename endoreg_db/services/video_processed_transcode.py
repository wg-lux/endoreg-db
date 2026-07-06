from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast
from uuid import uuid4

from django.db import transaction

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.streamable_media import sync_video_streamable_artifacts
from endoreg_db.services.video_files.io import ensure_local_processed_video_file
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.file_operations import ensure_directory, safe_unlink_file
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


def transcode_processed_video_for_storage_pressure(
    video: VideoFile,
    *,
    apply: bool,
    quality_mode: str = "balanced",
    force_cpu: bool = False,
    allow_larger: bool = False,
) -> ProcessedVideoTranscodeResult:
    old_processed_name = str(getattr(video.processed_file, "name", "") or "")
    old_hash = str(video.processed_video_hash or "")
    old_streamable_relative_path = str(video.processed_streamable_relative_path or "")

    if not old_processed_name:
        return ProcessedVideoTranscodeResult(
            video_id=video.pk,
            status="skipped_missing_processed_file",
            old_hash=old_hash,
            new_hash="",
            old_size=0,
            new_size=0,
            old_processed_name="",
            new_processed_name="",
            old_streamable_relative_path=old_streamable_relative_path,
            new_streamable_relative_path=old_streamable_relative_path,
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
        with ensure_local_processed_video_file(video) as source_path:
            source_path = Path(source_path)
            old_size = source_path.stat().st_size
            transcoded_path = transcode_video(
                source_path,
                output_path,
                quality_mode=quality_mode,
                force_cpu=force_cpu,
            )
            if transcoded_path is None:
                return ProcessedVideoTranscodeResult(
                    video_id=video.pk,
                    status="failed",
                    old_hash=old_hash,
                    new_hash="",
                    old_size=old_size,
                    new_size=0,
                    old_processed_name=old_processed_name,
                    new_processed_name="",
                    old_streamable_relative_path=old_streamable_relative_path,
                    new_streamable_relative_path=old_streamable_relative_path,
                    detail="ffmpeg transcode failed",
                )

            new_size = Path(transcoded_path).stat().st_size
            if new_size <= 0:
                return ProcessedVideoTranscodeResult(
                    video_id=video.pk,
                    status="failed",
                    old_hash=old_hash,
                    new_hash="",
                    old_size=old_size,
                    new_size=new_size,
                    old_processed_name=old_processed_name,
                    new_processed_name="",
                    old_streamable_relative_path=old_streamable_relative_path,
                    new_streamable_relative_path=old_streamable_relative_path,
                    detail="transcoded output is empty",
                )
            if not allow_larger and new_size >= old_size:
                return ProcessedVideoTranscodeResult(
                    video_id=video.pk,
                    status="skipped_not_smaller",
                    old_hash=old_hash,
                    new_hash="",
                    old_size=old_size,
                    new_size=new_size,
                    old_processed_name=old_processed_name,
                    new_processed_name="",
                    old_streamable_relative_path=old_streamable_relative_path,
                    new_streamable_relative_path=old_streamable_relative_path,
                    detail="transcoded output is not smaller",
                )

            new_hash = get_video_hash(Path(transcoded_path))
            if new_hash == old_hash:
                return ProcessedVideoTranscodeResult(
                    video_id=video.pk,
                    status="skipped_same_hash",
                    old_hash=old_hash,
                    new_hash=new_hash,
                    old_size=old_size,
                    new_size=new_size,
                    old_processed_name=old_processed_name,
                    new_processed_name=old_processed_name,
                    old_streamable_relative_path=old_streamable_relative_path,
                    new_streamable_relative_path=old_streamable_relative_path,
                    detail="transcoded output hash matches existing processed hash",
                )

            if (
                type(video)
                .objects.filter(processed_video_hash=new_hash)
                .exclude(pk=video.pk)
                .exists()
            ):
                return ProcessedVideoTranscodeResult(
                    video_id=video.pk,
                    status="failed",
                    old_hash=old_hash,
                    new_hash=new_hash,
                    old_size=old_size,
                    new_size=new_size,
                    old_processed_name=old_processed_name,
                    new_processed_name="",
                    old_streamable_relative_path=old_streamable_relative_path,
                    new_streamable_relative_path=old_streamable_relative_path,
                    detail="processed_video_hash already exists on another video",
                )

            new_processed_name = _processed_storage_name(
                video=video,
                content_hash=new_hash,
            )

            if not apply:
                return ProcessedVideoTranscodeResult(
                    video_id=video.pk,
                    status="dry_run",
                    old_hash=old_hash,
                    new_hash=new_hash,
                    old_size=old_size,
                    new_size=new_size,
                    old_processed_name=old_processed_name,
                    new_processed_name=new_processed_name,
                    old_streamable_relative_path=old_streamable_relative_path,
                    new_streamable_relative_path=old_streamable_relative_path,
                    detail="would replace processed file and streamable artifact",
                )

            with transaction.atomic():
                save_local_file(
                    video.processed_file,
                    Path(transcoded_path),
                    name=new_processed_name,
                    save=False,
                    overwrite=True,
                )
                saved_new_processed_name = new_processed_name
                video.processed_video_hash = new_hash
                video.processed_streamable_relative_path = ""
                video.save(
                    update_fields=[
                        "processed_file",
                        "processed_video_hash",
                        "processed_streamable_relative_path",
                        "date_modified",
                    ]
                )
                sync_video_streamable_artifacts(
                    video,
                    include_raw=False,
                    include_processed=True,
                    save=True,
                )
                new_streamable_relative_path = str(
                    video.processed_streamable_relative_path or ""
                )
                transaction.on_commit(
                    lambda: _cleanup_replaced_processed_assets(
                        video_id=video.pk,
                        old_processed_name=old_processed_name,
                        new_processed_name=new_processed_name,
                        old_streamable_relative_path=old_streamable_relative_path,
                        new_streamable_relative_path=new_streamable_relative_path,
                    )
                )

            return ProcessedVideoTranscodeResult(
                video_id=video.pk,
                status="changed",
                old_hash=old_hash,
                new_hash=new_hash,
                old_size=old_size,
                new_size=new_size,
                old_processed_name=old_processed_name,
                new_processed_name=new_processed_name,
                old_streamable_relative_path=old_streamable_relative_path,
                new_streamable_relative_path=new_streamable_relative_path,
            )
    except Exception as exc:
        logger.exception("Failed to transcode processed video %s", video.pk)
        if saved_new_processed_name and saved_new_processed_name != old_processed_name:
            try:
                processed_file = cast(_ProcessedFileWithStorage, video.processed_file)
                storage = processed_file.storage
                if storage.exists(saved_new_processed_name):
                    storage.delete(saved_new_processed_name)
            except Exception:
                logger.warning(
                    "Failed to clean up orphaned transcoded processed file %s",
                    saved_new_processed_name,
                    exc_info=True,
                )
        return ProcessedVideoTranscodeResult(
            video_id=video.pk,
            status="failed",
            old_hash=old_hash,
            new_hash="",
            old_size=0,
            new_size=0,
            old_processed_name=old_processed_name,
            new_processed_name="",
            old_streamable_relative_path=old_streamable_relative_path,
            new_streamable_relative_path=old_streamable_relative_path,
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

"""
Django management command to fix video file paths in the database.
"""

import logging
from collections.abc import Iterable
from pathlib import Path
from types import NoneType
from typing import Protocol, TypeAlias, TypedDict, Unpack, cast

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction
from lx_dtypes.models.contracts import (
    VideoPathRepairFileIndex,
    VideoPathRepairFileInfoPayload,
)

from endoreg_db.models import VideoFile
from endoreg_db.services.streamable_media import sync_video_streamable_artifacts
from endoreg_db.utils.paths import STORAGE_DIR

logger = logging.getLogger(__name__)

JsonNull: TypeAlias = NoneType


class FixVideoPathsOptions(TypedDict):
    video_id: int | JsonNull
    dry_run: bool
    verbose: bool
    storage_dir: str | JsonNull


class _StorageExists(Protocol):
    def exists(self, name: str) -> bool: ...


class _StoredVideoFile(Protocol):
    name: str | JsonNull
    storage: _StorageExists


class _VideoPathRepairModel(Protocol):
    id: int
    video_hash: str
    raw_file: _StoredVideoFile

    def save(self, *, update_fields: list[str]) -> None: ...


class Command(BaseCommand):
    help = "Fix video file paths in the database to match actual file locations"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--video-id",
            type=int,
            help="Fix specific video ID only",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be changed without making changes",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Enable verbose output",
        )
        parser.add_argument(
            "--storage-dir",
            type=str,
            default=None,
            help=f"Path to the storage directory (default: {STORAGE_DIR})",
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[FixVideoPathsOptions],
    ) -> None:
        """
        Synchronizes video file paths in the database with actual files on disk, updating broken or missing paths as needed.

        Scans the specified storage directory for video files, matches them to database records by UUID, and updates the `raw_file` field for videos whose stored path is missing or incorrect. Supports dry-run and verbose modes, and can process all videos or a specific video by ID.
        """
        dry_run = options["dry_run"]
        verbose = options["verbose"]
        video_id = options["video_id"]

        # Determine storage_dir from argument, env, or fallback
        storage_option = options["storage_dir"]
        storage_dir = (
            Path(storage_option) if storage_option is not None else STORAGE_DIR
        )

        # Find all actual video files
        actual_files: VideoPathRepairFileIndex = {}
        for pattern in ["**/*.mp4", "**/*.avi", "**/*.mov", "**/*.mkv"]:
            for file_path in storage_dir.glob(pattern):
                if file_path.is_file() and file_path.stat().st_size > 0:
                    # Extract UUID from filename
                    filename = file_path.name
                    # UUID is typically the first part before underscore or the whole name
                    if "_" in filename:
                        uuid_part = filename.split("_")[0]
                    else:
                        uuid_part = filename.split(".")[0]

                    # Store relative path from storage directory
                    relative_path = file_path.relative_to(storage_dir)
                    actual_files[uuid_part] = VideoPathRepairFileInfoPayload(
                        absolute_path=file_path,
                        relative_path=relative_path,
                        size_mb=file_path.stat().st_size / (1024 * 1024),
                    )

        self.stdout.write(f"Found {len(actual_files)} video files in storage")

        # Query videos to fix
        if video_id:
            try:
                videos: Iterable[VideoFile] = [VideoFile.objects.get(pk=video_id)]
                self.stdout.write(f"Processing specific video ID: {video_id}")
            except VideoFile.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f"Video with ID {video_id} not found")
                )
                return
        else:
            videos = VideoFile.objects.all()
            self.stdout.write(f"Processing {videos.count()} videos...")

        fixed_count = 0
        skipped_count = 0
        error_count = 0

        for raw_video in videos:
            video = cast(_VideoPathRepairModel, raw_video)
            try:
                uuid_str = str(video.video_hash)

                # Check if we have a matching file
                if uuid_str in actual_files:
                    file_info = actual_files[uuid_str]

                    # Check current file path
                    current_path_exists = False
                    current_path: str | JsonNull = None

                    if video.raw_file:
                        try:
                            current_path = video.raw_file.name
                            current_path_exists = bool(
                                current_path
                                and video.raw_file.storage.exists(current_path)
                            )
                        except (ValueError, AttributeError, OSError):
                            current_path_exists = False

                    if not current_path_exists:
                        # File path is broken, fix it
                        if verbose:
                            self.stdout.write(f"Video {video.id} ({uuid_str}):")
                            self.stdout.write(
                                f"  Current: {current_path or 'None'} (broken)"
                            )
                            self.stdout.write(
                                f"  Found: {file_info.absolute_path} ({file_info.size_mb:.1f} MB)"
                            )

                        if not dry_run:
                            with transaction.atomic():
                                # Update the raw_file path
                                video.raw_file.name = str(file_info.relative_path)
                                video.save(update_fields=["raw_file"])
                            try:
                                sync_video_streamable_artifacts(
                                    raw_video,
                                    include_raw=True,
                                    include_processed=False,
                                    save=True,
                                )
                            except Exception as exc:
                                self.stdout.write(
                                    self.style.WARNING(
                                        f"⚠️ Streamable sync failed for video {video.id}: {exc}"
                                    )
                                )

                            self.stdout.write(
                                self.style.SUCCESS(
                                    f"✅ Fixed video {video.id}: {file_info.relative_path}"
                                )
                            )
                        else:
                            self.stdout.write(
                                self.style.WARNING(
                                    f"🔄 Would fix video {video.id}: {file_info.relative_path}"
                                )
                            )

                        fixed_count += 1
                    else:
                        if verbose:
                            self.stdout.write(
                                f"✅ Video {video.id} ({uuid_str}) already has correct path"
                            )
                        skipped_count += 1
                else:
                    if verbose:
                        self.stdout.write(
                            f"❌ Video {video.id} ({uuid_str}): No matching file found"
                        )
                    error_count += 1

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"❌ Error processing video {video.id}: {e}")
                )
                error_count += 1

        # Summary
        self.stdout.write(f"\n{'=' * 50}")
        self.stdout.write(self.style.SUCCESS("SUMMARY"))
        self.stdout.write(f"{'=' * 50}")

        action_word = "Would fix" if dry_run else "Fixed"
        self.stdout.write(f"🔧 {action_word}: {fixed_count} videos")
        self.stdout.write(f"✅ Already correct: {skipped_count} videos")
        self.stdout.write(f"❌ Errors/Missing files: {error_count} videos")

        if dry_run and fixed_count > 0:
            self.stdout.write("\n💡 Run without --dry-run to apply changes")
        elif not dry_run and fixed_count > 0:
            self.stdout.write(
                f"\n🎉 Successfully fixed {fixed_count} video file paths!"
            )
            self.stdout.write("🔄 Restart your Django server to reload file paths")

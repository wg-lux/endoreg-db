import os
import shutil
import logging
from pathlib import Path
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand, CommandError
from endoreg_db.models import VideoFile
from endoreg_db.utils.paths import PROTECTED_DATA_ROOT, data_paths
from endoreg_db.utils.file_operations import (
    atomic_write_file,
    safe_rmtree,
    safe_unlink_file,
)
from endoreg_db.utils.storage import delete_field_file

logger = logging.getLogger(__name__)

FRAME_CLEANUP_COMPLETE_STATUSES = (
    "done_processing_anonymization",
    "validated",
    "anonymized",
)


class Command(BaseCommand):
    help = "Automated storage management and cleanup to prevent disk space issues"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be cleaned without actually deleting files",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force cleanup even if not critically low on space",
        )
        parser.add_argument(
            "--cleanup-frames",
            action="store_true",
            help="Clean up extracted video frames after processing",
        )
        parser.add_argument(
            "--cleanup-old-videos",
            action="store_true",
            help="Clean up old processed videos (keep raw files)",
        )
        parser.add_argument(
            "--cleanup-uploads",
            action="store_true",
            help="Clean up old upload cache files",
        )
        parser.add_argument(
            "--cleanup-logs",
            action="store_true",
            help="Clean up old log files",
        )
        parser.add_argument(
            "--max-age-days",
            type=int,
            default=30,
            help="Maximum age in days for files to keep (default: 30)",
        )
        parser.add_argument(
            "--emergency-threshold",
            type=float,
            default=95.0,
            help="Storage usage percentage that triggers emergency cleanup (default: 95%%)",
        )

    def handle(self, *args, **options):
        """Main command handler for storage management."""
        self.dry_run = options["dry_run"]
        self.force = options["force"]
        self.max_age_days = options["max_age_days"]
        self.emergency_threshold = options["emergency_threshold"]

        # Validate emergency_threshold range
        if not (0 <= self.emergency_threshold <= 100):
            raise CommandError(
                "The --emergency-threshold value must be between 0 and 100 (inclusive)."
            )

        if self.dry_run:
            self.stdout.write(
                self.style.WARNING("DRY RUN MODE - No files will be deleted")
            )

        try:
            # Check current storage status
            storage_info = self.get_storage_info()
            self.display_storage_status(storage_info)

            # Determine if emergency cleanup is needed
            needs_emergency_cleanup = (
                storage_info["usage_percent"] >= self.emergency_threshold or self.force
            )

            if needs_emergency_cleanup:
                self.stdout.write(
                    self.style.ERROR(
                        f"🚨 EMERGENCY CLEANUP TRIGGERED - Storage at {storage_info['usage_percent']:.1f}%"
                    )
                )
                self.emergency_cleanup(options)
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✅ Storage usage is acceptable at {storage_info['usage_percent']:.1f}%"
                    )
                )

            # Always run maintenance cleanup if requested
            if any(
                [
                    options["cleanup_frames"],
                    options["cleanup_old_videos"],
                    options["cleanup_uploads"],
                    options["cleanup_logs"],
                ]
            ):
                self.maintenance_cleanup(options)

            # Show final storage status
            final_storage = self.get_storage_info()
            self.display_cleanup_summary(storage_info, final_storage)

        except Exception as e:
            logger.error(f"Storage management failed: {e}")
            raise CommandError(f"Storage management failed: {e}")

    def get_storage_info(self):
        """Get current storage information."""
        try:
            # Get storage stats for the root filesystem
            total, used, free = shutil.disk_usage("/")
            usage_percent = (used / total) * 100

            # Get project-specific storage info
            protected_root = self.get_protected_root()
            project_storage = self.get_directory_size(protected_root)

            return {
                "total_gb": total / (1024**3),
                "used_gb": used / (1024**3),
                "free_gb": free / (1024**3),
                "usage_percent": usage_percent,
                "project_storage_gb": project_storage / (1024**3),
                "critical": usage_percent >= 95.0,
                "warning": usage_percent >= 85.0,
            }
        except Exception as e:
            logger.error(f"Failed to get storage info: {e}")
            raise

    def get_directory_size(self, path):
        """Calculate total size of a directory."""
        total_size = 0
        try:
            for dirpath, dirnames, filenames in os.walk(path):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    try:
                        total_size += os.path.getsize(filepath)
                    except (OSError, IOError):
                        continue
        except Exception:
            pass
        return total_size

    def get_storage_root(self) -> Path:
        """Return the canonical storage root from centralized path helpers."""
        return data_paths["storage"]

    def get_protected_root(self) -> Path:
        """Return the canonical protected runtime root."""
        return PROTECTED_DATA_ROOT

    def get_frames_dir(self) -> Path:
        """Return the canonical extracted-frames directory."""
        return data_paths["frame"]

    def get_uploads_dir(self) -> Path:
        """Return the upload cache directory under the canonical storage root."""
        return self.get_storage_root() / "uploads"

    def get_temp_dirs(self) -> list[Path]:
        """Return temp directories worth cleaning."""
        return [
            Path("/tmp"),
            self.get_protected_root() / "tmp",
            self.get_storage_root() / "temp",
        ]

    def display_storage_status(self, storage_info):
        """Display current storage status."""
        status_color = (
            self.style.ERROR
            if storage_info["critical"]
            else self.style.WARNING
            if storage_info["warning"]
            else self.style.SUCCESS
        )

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(status_color("💾 STORAGE STATUS"))
        self.stdout.write("=" * 60)
        self.stdout.write(f"Total Space: {storage_info['total_gb']:.1f} GB")
        self.stdout.write(f"Used Space:  {storage_info['used_gb']:.1f} GB")
        self.stdout.write(f"Free Space:  {storage_info['free_gb']:.1f} GB")
        self.stdout.write(
            status_color(f"Usage:       {storage_info['usage_percent']:.1f}%")
        )
        self.stdout.write(f"Project Size: {storage_info['project_storage_gb']:.1f} GB")

        if storage_info["critical"]:
            self.stdout.write(self.style.ERROR("🚨 CRITICAL: Storage critically low!"))
        elif storage_info["warning"]:
            self.stdout.write(self.style.WARNING("⚠️  WARNING: Storage getting low"))

    def emergency_cleanup(self, options):
        """Perform emergency cleanup to free critical storage space."""
        self.stdout.write(self.style.ERROR("\n🚨 PERFORMING EMERGENCY CLEANUP"))

        total_freed = 0

        # 1. AGGRESSIVE: Clean up ALL extracted frames (usually largest space saver)
        frames_freed = self.cleanup_all_extracted_frames()
        total_freed += frames_freed

        # 2. AGGRESSIVE: Clean up ALL upload cache
        uploads_freed = self.cleanup_all_uploads()
        total_freed += uploads_freed

        # 3. Clean up old logs
        logs_freed = self.cleanup_old_logs()
        total_freed += logs_freed

        # 4. Clean up temporary files
        temp_freed = self.cleanup_temp_files()
        total_freed += temp_freed

        # 5. If still critical, clean up ALL processed videos (more aggressive)
        storage_info = self.get_storage_info()
        if storage_info["usage_percent"] >= 90.0:
            # Use 0 days to clean up ALL processed videos
            videos_freed = self.cleanup_all_processed_videos()
            total_freed += videos_freed

        self.stdout.write(
            self.style.SUCCESS(
                f"✅ Emergency cleanup completed: {total_freed / (1024**3):.2f} GB freed"
            )
        )

    def maintenance_cleanup(self, options):
        """Perform regular maintenance cleanup."""
        self.stdout.write(self.style.SUCCESS("\n🔧 PERFORMING MAINTENANCE CLEANUP"))

        total_freed = 0

        if options["cleanup_frames"]:
            freed = self.cleanup_extracted_frames()
            total_freed += freed

        if options["cleanup_old_videos"]:
            freed = self.cleanup_old_processed_videos(self.max_age_days)
            total_freed += freed

        if options["cleanup_uploads"]:
            freed = self.cleanup_upload_cache()
            total_freed += freed

        if options["cleanup_logs"]:
            freed = self.cleanup_old_logs()
            total_freed += freed

        self.stdout.write(
            self.style.SUCCESS(
                f"✅ Maintenance cleanup completed: {total_freed / (1024**3):.2f} GB freed"
            )
        )

    def cleanup_extracted_frames(self):
        """Clean up extracted video frames that are no longer needed."""
        self.stdout.write("🖼️  Cleaning up extracted video frames...")

        total_freed = 0
        frames_dir = self.get_frames_dir()

        if not frames_dir.exists():
            return 0

        # Find videos that have completed processing
        completed_videos = VideoFile.objects.filter(
            anonymization_tasks__status__in=FRAME_CLEANUP_COMPLETE_STATUSES
        ).distinct()

        for video in completed_videos:
            try:
                # Find frame directories for this video
                video_frame_dirs = list(frames_dir.glob(f"*{video.uuid}*"))

                for frame_dir in video_frame_dirs:
                    if frame_dir.is_dir():
                        dir_size = self.get_directory_size(frame_dir)

                        if not self.dry_run:
                            safe_rmtree(frame_dir, missing_ok=True)

                        total_freed += dir_size
                        self.stdout.write(
                            f"  Removed frames for {video.uuid}: {dir_size / (1024**2):.1f} MB"
                        )

            except Exception as e:
                logger.warning(f"Failed to clean frames for video {video.uuid}: {e}")
                continue

        self.stdout.write(f"✅ Frames cleanup: {total_freed / (1024**3):.2f} GB freed")
        return total_freed

    def cleanup_upload_cache(self):
        """Clean up old upload cache files."""
        self.stdout.write("📤 Cleaning up upload cache...")

        total_freed = 0
        uploads_dir = self.get_uploads_dir()

        if not uploads_dir.exists():
            return 0

        cutoff_date = datetime.now() - timedelta(days=self.max_age_days)

        for file_path in uploads_dir.rglob("*"):
            if file_path.is_file():
                try:
                    file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime)

                    if file_mtime < cutoff_date:
                        file_size = file_path.stat().st_size

                        if not self.dry_run:
                            safe_unlink_file(file_path, missing_ok=False)

                        total_freed += file_size

                except Exception as e:
                    logger.warning(f"Failed to clean upload file {file_path}: {e}")
                    continue

        self.stdout.write(
            f"✅ Upload cache cleanup: {total_freed / (1024**3):.2f} GB freed"
        )
        return total_freed

    def cleanup_old_logs(self):
        """Clean up old log files."""
        self.stdout.write("📋 Cleaning up old log files...")

        total_freed = 0
        project_root = Path(__file__).resolve().parents[3]

        # Find and clean large log files
        for log_file in project_root.rglob("*.log"):
            try:
                if log_file.stat().st_size > 10 * 1024 * 1024:  # Files larger than 10MB
                    file_size = log_file.stat().st_size

                    if not self.dry_run:
                        atomic_write_file(destination=log_file, content=(b"",))

                    total_freed += file_size
                    self.stdout.write(
                        f"  Truncated {log_file}: {file_size / (1024**2):.1f} MB"
                    )

            except Exception as e:
                logger.warning(f"Failed to clean log file {log_file}: {e}")
                continue

        self.stdout.write(f"✅ Log cleanup: {total_freed / (1024**3):.2f} GB freed")
        return total_freed

    def cleanup_temp_files(self):
        """Clean up temporary files."""
        self.stdout.write("🗂️  Cleaning up temporary files...")

        total_freed = 0
        temp_dirs = self.get_temp_dirs()

        for temp_dir in temp_dirs:
            if not Path(temp_dir).exists():
                continue

            try:
                for item in Path(temp_dir).glob("*"):
                    if item.is_file() and item.name.startswith(("tmp", "temp")):
                        file_size = item.stat().st_size

                        if not self.dry_run:
                            safe_unlink_file(item, missing_ok=False)

                        total_freed += file_size

            except Exception as e:
                logger.warning(f"Failed to clean temp dir {temp_dir}: {e}")
                continue

        self.stdout.write(
            f"✅ Temp files cleanup: {total_freed / (1024**3):.2f} GB freed"
        )
        return total_freed

    def cleanup_old_processed_videos(self, max_age_days):
        """Clean up old processed videos while keeping raw files."""
        self.stdout.write(
            f"🎥 Cleaning up processed videos older than {max_age_days} days..."
        )

        total_freed = 0
        cutoff_date = datetime.now() - timedelta(days=max_age_days)

        # Find old processed videos - fix the date filtering
        try:
            old_videos = VideoFile.objects.filter(
                created_at__lt=cutoff_date,  # Use created_at instead of date_created
                processed_file__isnull=False,
            ).exclude(
                anonymization_tasks__status__in=[
                    "processing_anonymization",
                    "extracting_frames",
                ]
            )

            self.stdout.write(
                f"Found {old_videos.count()} processed videos older than {max_age_days} days"
            )

        except Exception:
            # Fallback: try different date field names
            try:
                old_videos = VideoFile.objects.filter(
                    processed_file__isnull=False
                ).exclude(
                    anonymization_tasks__status__in=[
                        "processing_anonymization",
                        "extracting_frames",
                    ]
                )
                self.stdout.write(
                    f"Using fallback filter, found {old_videos.count()} processed videos"
                )
            except Exception as e2:
                logger.error(f"Failed to query videos: {e2}")
                return total_freed

        for video in old_videos:
            try:
                processed_field = getattr(video, "processed_file", None)
                if processed_field and getattr(processed_field, "name", None):
                    file_size = 0
                    try:
                        processed_path = Path(processed_field.path)
                    except Exception:
                        processed_path = None
                    if processed_path and processed_path.exists():
                        file_size = processed_path.stat().st_size

                    if not self.dry_run:
                        delete_field_file(processed_field, missing_ok=True, save=False)
                        video.processed_file = None
                        video.save(update_fields=["processed_file"])

                    total_freed += file_size
                    self.stdout.write(
                        f"  Removed processed video {video.uuid}: {file_size / (1024**2):.1f} MB"
                    )

            except Exception as e:
                logger.warning(f"Failed to clean processed video {video.uuid}: {e}")
                continue

        self.stdout.write(
            f"✅ Processed videos cleanup: {total_freed / (1024**3):.2f} GB freed"
        )
        return total_freed

    def cleanup_all_extracted_frames(self):
        """More aggressive cleanup - remove ALL extracted frames regardless of status."""
        self.stdout.write("🖼️  AGGRESSIVE: Cleaning up ALL extracted video frames...")

        total_freed = 0
        frames_dir = self.get_frames_dir()

        if not frames_dir.exists():
            self.stdout.write("No frames directory found")
            return 0

        try:
            # Get directory size before cleanup
            initial_size = self.get_directory_size(frames_dir)
            self.stdout.write(
                f"Found frames directory with {initial_size / (1024**3):.2f} GB"
            )

            # Remove all frame directories and files
            for item in frames_dir.iterdir():
                if item.is_dir():
                    dir_size = self.get_directory_size(item)

                    if not self.dry_run:
                        safe_rmtree(item, missing_ok=True)

                    total_freed += dir_size
                    self.stdout.write(
                        f"  Removed frame directory {item.name}: {dir_size / (1024**2):.1f} MB"
                    )
                elif item.is_file():
                    file_size = item.stat().st_size

                    if not self.dry_run:
                        safe_unlink_file(item, missing_ok=False)

                    total_freed += file_size
                    self.stdout.write(
                        f"  Removed frame file {item.name}: {file_size / (1024**2):.1f} MB"
                    )

        except Exception as e:
            logger.error(f"Failed to clean frames directory: {e}")

        self.stdout.write(
            f"✅ Aggressive frames cleanup: {total_freed / (1024**3):.2f} GB freed"
        )
        return total_freed

    def cleanup_all_uploads(self):
        """More aggressive cleanup - remove ALL upload cache files."""
        self.stdout.write("📤 AGGRESSIVE: Cleaning up ALL upload cache...")

        total_freed = 0
        uploads_dir = self.get_uploads_dir()

        if not uploads_dir.exists():
            return 0

        try:
            for item in uploads_dir.rglob("*"):
                if item.is_file():
                    file_size = item.stat().st_size

                    if not self.dry_run:
                        safe_unlink_file(item, missing_ok=False)

                    total_freed += file_size

        except Exception as e:
            logger.error(f"Failed to clean uploads directory: {e}")

        self.stdout.write(
            f"✅ Aggressive upload cleanup: {total_freed / (1024**3):.2f} GB freed"
        )
        return total_freed

    def cleanup_all_processed_videos(self):
        """AGGRESSIVE: Clean up ALL processed videos while keeping raw files."""
        self.stdout.write("🎥 AGGRESSIVE: Cleaning up ALL processed videos...")

        total_freed = 0

        try:
            # Find ALL processed videos
            processed_videos = VideoFile.objects.filter(
                processed_file__isnull=False
            ).exclude(
                anonymization_tasks__status__in=[
                    "processing_anonymization",
                    "extracting_frames",
                ]
            )

            self.stdout.write(
                f"Found {processed_videos.count()} processed videos to clean"
            )

            for video in processed_videos:
                try:
                    freed = self._cleanup_processed_video_file(video)
                    total_freed += freed
                except Exception as e:
                    logger.warning(f"Failed to clean processed video {video.uuid}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Failed to query processed videos: {e}")

        self.stdout.write(
            f"✅ Aggressive processed videos cleanup: {total_freed / (1024**3):.2f} GB freed"
        )
        return total_freed

    def _cleanup_processed_video_file(self, video):
        """
        Helper to clean up a single processed video file, update DB, and return freed size in bytes.
        """
        processed_field = getattr(video, "processed_file", None)
        if processed_field and getattr(processed_field, "name", None):
            file_size = 0
            try:
                processed_path = Path(processed_field.path)
            except Exception:
                processed_path = None
            if processed_path and processed_path.exists():
                file_size = processed_path.stat().st_size
            if not self.dry_run:
                delete_field_file(processed_field, missing_ok=True, save=False)
                video.processed_file = None
                video.save(update_fields=["processed_file"])
            self.stdout.write(
                f"  Removed processed video {video.uuid}: {file_size / (1024**2):.1f} MB"
            )
            return file_size
        return 0

    def display_cleanup_summary(self, before, after):
        """Display cleanup summary."""
        freed_gb = before["used_gb"] - after["used_gb"]

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("📊 CLEANUP SUMMARY"))
        self.stdout.write("=" * 60)
        self.stdout.write(
            f"Before: {before['used_gb']:.1f} GB used ({before['usage_percent']:.1f}%)"
        )
        self.stdout.write(
            f"After:  {after['used_gb']:.1f} GB used ({after['usage_percent']:.1f}%)"
        )
        self.stdout.write(self.style.SUCCESS(f"Freed:  {freed_gb:.2f} GB"))

        if after["usage_percent"] < 90.0:
            self.stdout.write(self.style.SUCCESS("✅ Storage levels are now healthy!"))
        elif after["usage_percent"] < 95.0:
            self.stdout.write(
                self.style.WARNING("⚠️  Storage levels improved but still high")
            )
        else:
            self.stdout.write(
                self.style.ERROR(
                    "🚨 Storage levels still critical - manual intervention needed"
                )
            )

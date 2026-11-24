import os
import shutil
import logging
from pathlib import Path
from typing import NoReturn

from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import ValidationError

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.utils.paths import get_runtime_paths
from lx_dtypes.models.contracts.management_command import (
    StorageManagementCommandOptionsPayload,
    StorageManagementInfoPayload,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Read-only storage status; legacy cleanup is disabled"
    dry_run: bool
    force: bool
    max_age_days: int
    emergency_threshold: float

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show storage status; legacy cleanup remains disabled",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Disabled legacy cleanup request; always fails closed",
        )
        parser.add_argument(
            "--cleanup-frames",
            action="store_true",
            help="Disabled legacy frame cleanup",
        )
        parser.add_argument(
            "--cleanup-old-videos",
            action="store_true",
            help="Disabled legacy processed-video cleanup",
        )
        parser.add_argument(
            "--cleanup-uploads",
            action="store_true",
            help="Disabled legacy upload cleanup",
        )
        parser.add_argument(
            "--cleanup-logs",
            action="store_true",
            help="Disabled legacy log cleanup",
        )
        parser.add_argument(
            "--max-age-days",
            type=int,
            default=30,
            help="Legacy option retained for compatibility; cleanup is disabled",
        )
        parser.add_argument(
            "--emergency-threshold",
            type=float,
            default=95.0,
            help="Storage usage percentage that causes a fail-closed error (default: 95%%)",
        )

    def handle(self, *args: object, **options: object) -> None:
        """Main command handler for storage management."""
        try:
            options_payload = StorageManagementCommandOptionsPayload.model_validate(
                options
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        self.dry_run = options_payload.dry_run
        self.force = options_payload.force
        self.max_age_days = options_payload.max_age_days
        self.emergency_threshold = options_payload.emergency_threshold

        if self.dry_run:
            self.stdout.write(
                self.style.WARNING("DRY RUN MODE - No files will be deleted")
            )

        try:
            storage_info = self.get_storage_info()
            self.display_storage_status(storage_info)
            if (
                storage_info.usage_percent >= self.emergency_threshold
                or self.force
                or options_payload.cleanup_frames
                or options_payload.cleanup_old_videos
                or options_payload.cleanup_uploads
                or options_payload.cleanup_logs
            ):
                self._reject_legacy_cleanup()

        except CommandError:
            raise
        except Exception as exc:
            logger.exception(f"Storage management failed: {exc}")
            raise CommandError(f"Storage management failed: {exc}") from exc

    def get_storage_info(self) -> StorageManagementInfoPayload:
        """Get current storage information."""
        try:
            # Measure the filesystem that contains the canonical media storage.
            total, used, free = shutil.disk_usage(self.get_storage_root())
            usage_percent = (used / total) * 100

            # Get project-specific storage info
            protected_root = self.get_protected_root()
            project_storage = self.get_directory_size(protected_root)

            return StorageManagementInfoPayload(
                total_gb=total / (1024**3),
                used_gb=used / (1024**3),
                free_gb=free / (1024**3),
                usage_percent=usage_percent,
                project_storage_gb=project_storage / (1024**3),
                critical=usage_percent >= 95.0,
                warning=usage_percent >= 85.0,
            )
        except (OSError, ValidationError, ZeroDivisionError) as exc:
            logger.exception(f"Failed to get storage info: {exc}")
            raise

    def get_directory_size(self, path: Path) -> int:
        """Calculate total size of a directory."""
        total_size = 0
        try:
            for dirpath, _, filenames in os.walk(path):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    try:
                        total_size += os.path.getsize(filepath)
                    except OSError:
                        continue
        except OSError:
            pass
        return total_size

    def get_storage_root(self) -> Path:
        """Return the canonical storage root from centralized path helpers."""
        return get_runtime_paths().storage

    def get_protected_root(self) -> Path:
        """Return the canonical protected runtime root."""
        return get_runtime_paths().runtime_root

    def display_storage_status(
        self, storage_info: StorageManagementInfoPayload
    ) -> None:
        """Display current storage status."""
        status_color = (
            self.style.ERROR
            if storage_info.critical
            else self.style.WARNING
            if storage_info.warning
            else self.style.SUCCESS
        )

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(status_color("💾 STORAGE STATUS"))
        self.stdout.write("=" * 60)
        self.stdout.write(f"Total Space: {storage_info.total_gb:.1f} GB")
        self.stdout.write(f"Used Space:  {storage_info.used_gb:.1f} GB")
        self.stdout.write(f"Free Space:  {storage_info.free_gb:.1f} GB")
        self.stdout.write(
            status_color(f"Usage:       {storage_info.usage_percent:.1f}%")
        )
        self.stdout.write(f"Project Size: {storage_info.project_storage_gb:.1f} GB")

        if storage_info.critical:
            self.stdout.write(self.style.ERROR("🚨 CRITICAL: Storage critically low!"))
        elif storage_info.warning:
            self.stdout.write(self.style.WARNING("⚠️  WARNING: Storage getting low"))

    def _reject_legacy_cleanup(self) -> NoReturn:
        raise CommandError(
            "Legacy storage_management cleanup is disabled: generation, media-operation "
            "lease and file-ownership checks are required. Use normalize_video_storage "
            "for read-only inventory and reap_processed_video_generations for recorded "
            "replacement cleanup. See docs/video_storage_normalization.md."
        )

    def emergency_cleanup(self) -> None:
        """Reject legacy cleanup without generation and ownership evidence."""
        self._reject_legacy_cleanup()

    def maintenance_cleanup(
        self, options: StorageManagementCommandOptionsPayload
    ) -> None:
        """Reject legacy cleanup without generation and ownership evidence."""
        self._reject_legacy_cleanup()

    def cleanup_extracted_frames(self) -> int:
        """Reject legacy cleanup without generation and ownership evidence."""
        self._reject_legacy_cleanup()

    def cleanup_upload_cache(self) -> int:
        """Reject legacy cleanup without generation and ownership evidence."""
        self._reject_legacy_cleanup()

    def cleanup_old_logs(self) -> int:
        """Reject legacy cleanup without generation and ownership evidence."""
        self._reject_legacy_cleanup()

    def cleanup_temp_files(self) -> int:
        """Reject legacy cleanup without generation and ownership evidence."""
        self._reject_legacy_cleanup()

    def cleanup_old_processed_videos(self, max_age_days: int) -> int:
        """Reject legacy cleanup without generation and ownership evidence."""
        self._reject_legacy_cleanup()

    def cleanup_all_extracted_frames(self) -> int:
        """Reject legacy cleanup without generation and ownership evidence."""
        self._reject_legacy_cleanup()

    def cleanup_all_uploads(self) -> int:
        """Reject legacy cleanup without generation and ownership evidence."""
        self._reject_legacy_cleanup()

    def cleanup_all_processed_videos(self) -> int:
        """Reject legacy cleanup without generation and ownership evidence."""
        self._reject_legacy_cleanup()

    def _cleanup_processed_video_file(self, video: VideoFile) -> int:
        """Reject legacy cleanup without generation and ownership evidence."""
        self._reject_legacy_cleanup()

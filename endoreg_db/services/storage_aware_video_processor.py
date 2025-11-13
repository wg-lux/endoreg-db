import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from django.db import transaction

from endoreg_db.exceptions import InsufficientStorageError
from endoreg_db.models import AnonymizationTask, VideoFile

# from endoreg_db.models.media.video.create_from_file import check_storage_capacity

logger = logging.getLogger(__name__)


class StorageAwareVideoProcessor:
    """
    Enhanced video processor with built-in storage management and automatic cleanup.
    """

    def __init__(self, auto_cleanup: bool = True, min_free_space_gb: float = 10.0):
        self.auto_cleanup = auto_cleanup
        self.min_free_space_gb = min_free_space_gb

    def check_and_ensure_storage(self, required_space_estimate: int = None) -> bool:
        """
        Check storage capacity and perform cleanup if needed.

        Args:
            required_space_estimate: Estimated space needed in bytes

        Returns:
            True if enough space is available, False otherwise

        Raises:
            InsufficientStorageError: If space cannot be freed
        """
        try:
            # Get current storage stats
            total, used, free = shutil.disk_usage("/")
            free_gb = free / (1024**3)
            usage_percent = (used / total) * 100

            logger.info(
                f"Storage check: {free_gb:.1f}GB free ({usage_percent:.1f}% used)"
            )

            # Check if we need emergency cleanup
            needs_cleanup = (
                usage_percent >= 95.0
                or free_gb < self.min_free_space_gb
                or (required_space_estimate and free < required_space_estimate * 1.2)
            )

            if needs_cleanup and self.auto_cleanup:
                logger.warning(
                    f"Storage critically low ({usage_percent:.1f}%), performing automatic cleanup"
                )
                self._perform_emergency_cleanup()

                # Re-check after cleanup
                total, used, free = shutil.disk_usage("/")
                free_gb = free / (1024**3)
                usage_percent = (used / total) * 100

                if usage_percent >= 98.0:
                    raise InsufficientStorageError(
                        f"Storage critically low even after cleanup: {usage_percent:.1f}% used, "
                        f"only {free_gb:.1f}GB free",
                        required_space=required_space_estimate
                        or int(self.min_free_space_gb * 1024**3),
                        available_space=free,
                    )

            return True

        except Exception as e:
            logger.error(f"Storage check failed: {e}")
            raise

    def _perform_emergency_cleanup(self) -> int:
        """
        Perform emergency storage cleanup.

        Returns:
            Bytes freed
        """
        total_freed = 0

        try:
            # 1. Clean up extracted frames for completed videos
            frames_freed = self._cleanup_completed_video_frames()
            total_freed += frames_freed

            # 2. Clean up upload cache older than 1 day
            uploads_freed = self._cleanup_old_uploads(max_age_hours=24)
            total_freed += uploads_freed

            # 3. Clean up large log files
            logs_freed = self._cleanup_large_logs()
            total_freed += logs_freed

            # 4. Clean up temporary files
            temp_freed = self._cleanup_temp_files()
            total_freed += temp_freed

            logger.info(
                f"Emergency cleanup completed: {total_freed / (1024**3):.2f}GB freed"
            )

        except Exception as e:
            logger.error(f"Emergency cleanup failed: {e}")

        return total_freed

    def _cleanup_completed_video_frames(self) -> int:
        """Clean up frames for videos that have completed processing."""
        total_freed = 0

        try:
            from django.conf import settings

            frames_dir = Path(settings.BASE_DIR).parent / "storage" / "frames"

            if not frames_dir.exists():
                return 0

            # Find videos with completed anonymization
            completed_videos = VideoFile.objects.filter(
                anonymization_tasks__status="done_processing_anonymization"
            ).distinct()

            for video in completed_videos:
                # Find frame directories for this video
                video_frame_dirs = list(frames_dir.glob(f"*{video.uuid}*"))

                for frame_dir in video_frame_dirs:
                    if frame_dir.is_dir():
                        try:
                            # Calculate directory size
                            dir_size = sum(
                                f.stat().st_size
                                for f in frame_dir.rglob("*")
                                if f.is_file()
                            )

                            # Remove the directory
                            shutil.rmtree(frame_dir, ignore_errors=True)
                            total_freed += dir_size

                            logger.info(
                                f"Cleaned frames for {video.uuid}: {dir_size / (1024**2):.1f}MB"
                            )

                        except Exception as e:
                            logger.warning(
                                f"Failed to clean frames for {video.uuid}: {e}"
                            )

        except Exception as e:
            logger.error(f"Frame cleanup failed: {e}")

        return total_freed

    def _cleanup_old_uploads(self, max_age_hours: int = 24) -> int:
        """Clean up old upload cache files."""
        total_freed = 0

        try:
            from django.conf import settings

            uploads_dir = Path(settings.BASE_DIR).parent / "storage" / "uploads"

            if not uploads_dir.exists():
                return 0

            cutoff_time = datetime.now().timestamp() - (max_age_hours * 3600)

            for file_path in uploads_dir.rglob("*"):
                if file_path.is_file():
                    try:
                        if file_path.stat().st_mtime < cutoff_time:
                            file_size = file_path.stat().st_size
                            file_path.unlink()
                            total_freed += file_size

                    except Exception as e:
                        logger.warning(f"Failed to clean upload file {file_path}: {e}")

        except Exception as e:
            logger.error(f"Upload cleanup failed: {e}")

        return total_freed

    def _cleanup_large_logs(self, max_size_mb: int = 50) -> int:
        """Clean up large log files by truncating them."""
        total_freed = 0

        try:
            from django.conf import settings

            project_root = Path(settings.BASE_DIR).parent
            max_size_bytes = max_size_mb * 1024 * 1024

            for log_file in project_root.rglob("*.log"):
                try:
                    if log_file.stat().st_size > max_size_bytes:
                        original_size = log_file.stat().st_size

                        # Truncate the log file
                        with open(log_file, "w") as f:
                            f.write(
                                f"# Log truncated at {datetime.now()} due to size limit\n"
                            )

                        total_freed += original_size
                        logger.info(
                            f"Truncated log {log_file}: {original_size / (1024**2):.1f}MB"
                        )

                except Exception as e:
                    logger.warning(f"Failed to truncate log {log_file}: {e}")

        except Exception as e:
            logger.error(f"Log cleanup failed: {e}")

        return total_freed

    def _cleanup_temp_files(self) -> int:
        """Clean up temporary files."""
        total_freed = 0

        try:
            temp_patterns = [
                "/tmp/tmp*",
                "/tmp/temp*",
                "/tmp/django*",
                "/tmp/ffmpeg*",
            ]

            for pattern in temp_patterns:
                import glob

                for temp_file in glob.glob(pattern):
                    try:
                        temp_path = Path(temp_file)
                        if temp_path.is_file():
                            file_size = temp_path.stat().st_size
                            temp_path.unlink()
                            total_freed += file_size

                    except Exception as e:
                        logger.warning(f"Failed to clean temp file {temp_file}: {e}")

        except Exception as e:
            logger.error(f"Temp file cleanup failed: {e}")

        return total_freed

    def process_video_with_storage_management(
        self,
        video_file: VideoFile,
        cleanup_frames_after: bool = True,
        progress_callback: Optional[callable] = None,
    ) -> bool:
        """
        Process a video with automatic storage management.

        Args:
            video_file: VideoFile instance to process
            cleanup_frames_after: Whether to clean up frames after processing
            progress_callback: Optional progress callback function

        Returns:
            True if processing succeeded, False otherwise
        """
        try:
            # Estimate required space (rough calculation)
            video_size = 0
            if video_file.raw_file and hasattr(video_file.raw_file, "size"):
                video_size = video_file.raw_file.size

            # Estimate: frames ~= 3x video size, processing ~= 2x video size
            estimated_space_needed = video_size * 5

            # Check storage before starting
            self.check_and_ensure_storage(estimated_space_needed)

            # Update task status
            task = self._get_or_create_task(video_file)
            task.start_processing()

            if progress_callback:
                progress_callback(10, "Starting video processing...")

            # Run the actual video processing pipeline
            success = video_file.pipe_1(
                model_name="image_multilabel_classification_colonoscopy_default",
                delete_frames_after=cleanup_frames_after,
            )

            if success:
                if progress_callback:
                    progress_callback(90, "Processing completed, cleaning up...")

                # Mark as completed
                task.mark_completed("Video processing completed successfully")

                # Additional cleanup if requested
                if cleanup_frames_after:
                    self._cleanup_video_frames(video_file)

                if progress_callback:
                    progress_callback(100, "Processing completed successfully")

                logger.info(
                    f"Video processing completed successfully: {video_file.uuid}"
                )
                return True

            task.mark_failed("Video processing pipeline failed")
            logger.error(f"Video processing failed: {video_file.uuid}")
            return False

        except InsufficientStorageError as e:
            logger.error(f"Storage error during video processing: {e}")
            task = self._get_or_create_task(video_file)
            task.mark_failed(f"Insufficient storage: {e}")
            return False

        except Exception as e:
            logger.error(f"Video processing failed: {e}")
            task = self._get_or_create_task(video_file)
            task.mark_failed(f"Processing error: {e}")
            return False

    def _get_or_create_task(self, video_file: VideoFile) -> AnonymizationTask:
        """Get or create an anonymization task for the video."""
        task, created = AnonymizationTask.objects.get_or_create(
            video_file=video_file,
            defaults={
                "status": "not_started",
                "progress": 0,
                "message": "Initializing...",
            },
        )
        return task

    def _cleanup_video_frames(self, video_file: VideoFile):
        """Clean up frames for a specific video."""
        try:
            from django.conf import settings

            frames_dir = Path(settings.BASE_DIR).parent / "storage" / "frames"

            # Find frame directories for this video
            video_frame_dirs = list(frames_dir.glob(f"*{video_file.uuid}*"))

            for frame_dir in video_frame_dirs:
                if frame_dir.is_dir():
                    shutil.rmtree(frame_dir, ignore_errors=True)
                    logger.info(f"Cleaned up frames for video {video_file.uuid}")

        except Exception as e:
            logger.warning(f"Failed to clean up frames for {video_file.uuid}: {e}")


# Global instance for easy access
storage_aware_processor = StorageAwareVideoProcessor()

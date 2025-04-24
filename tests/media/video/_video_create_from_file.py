
from endoreg_db.models import VideoFile
from logging import getLogger
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .test_video_file import VideoFileModelTest

logger = getLogger("video_file")

def _test_video_create_from_file(test_case:"VideoFileModelTest"):
    """
    Test if a video file can be created from a file and cleaned up afterwards.
    """
    video_path = test_case.non_anonym_video_path
    default_center = test_case.center
    video_file = None  # Initialize video_file to None

    try:
        # Create the video file
        video_file = VideoFile.create_from_file(
            file_path=video_path,
            center_name=default_center.name,  # Pass center name as expected by _create_from_file
            delete_source=False  # Keep the original asset for other tests
        )

        # Assertions
        test_case.assertIsInstance(video_file, VideoFile)
        test_case.assertIsNotNone(video_file.pk, "VideoFile should have a primary key after saving.")
        test_case.assertTrue(video_file.has_raw, "VideoFile should have a raw file.")

        fp = video_file.active_file_path
        test_case.assertIsNotNone(fp, "Active file path should not be None.")
        db_video_file_exists = fp.exists()
        logger.info(f"Created video file {fp} exists: {db_video_file_exists}")
        test_case.assertTrue(db_video_file_exists, f"Video file {fp} should exist in storage.")

    finally:
        # Cleanup: Delete the video file and its associated file from storage
        if video_file and video_file.pk:
            logger.info(f"Cleaning up test video file (UUID: {video_file.uuid})")
            try:
                # Reload from DB to ensure we have the latest state before deleting
                video_to_delete = VideoFile.objects.get(pk=video_file.pk)
                video_to_delete.delete_with_file()
                logger.info(f"Successfully cleaned up test video file (UUID: {video_file.uuid})")
                # Optional: Assert the file no longer exists
                if fp:
                    test_case.assertFalse(fp.exists(), f"Video file {fp} should have been deleted.")
            except VideoFile.DoesNotExist:
                logger.warning(f"VideoFile with pk {video_file.pk} not found for cleanup, might have failed creation.")
            except Exception as e:
                logger.error(f"Error during cleanup of video file (UUID: {video_file.uuid}): {e}", exc_info=True)



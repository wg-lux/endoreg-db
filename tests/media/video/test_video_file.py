from .helper import get_random_video_path_by_examination_alias
from django.test import TestCase
from logging import getLogger
from ._video_create_from_file import _test_video_create_from_file

from endoreg_db.utils.video.ffmpeg_wrapper import is_ffmpeg_available

from endoreg_db.models import (
    VideoFile  # Import Frame model
)
import logging
from django.conf import settings

RUN_VIDEO_TESTS = settings.RUN_VIDEO_TESTS
assert isinstance(RUN_VIDEO_TESTS, bool), "RUN_VIDEO_TESTS must be a boolean value"


logger = getLogger("video_file")
logger.setLevel(logging.WARNING)

from ...helpers.default_objects import get_default_center

from ...helpers.data_loader import (
    load_disease_data,
    load_event_data,
    load_information_source,
    load_examination_data,
    load_center_data,
    load_endoscope_data,
    load_ai_model_label_data,
    load_ai_model_data,
)

FFMPEG_AVAILABLE = is_ffmpeg_available()


class VideoFileModelTest(TestCase):
    def setUp(self):
        load_disease_data()
        load_event_data()
        load_information_source()
        load_examination_data()
        load_center_data()
        load_endoscope_data()
        load_ai_model_label_data()
        load_ai_model_data()

        # Provide examination_alias for a more specific search
        self.non_anonym_video_path = get_random_video_path_by_examination_alias(
            examination_alias='egd', is_anonymous=False
        )
        self.center = get_default_center()

    def test_random_video_exists(self):
        """
        Test if all video files exist.
        """
        video_path = self.non_anonym_video_path
        self.assertTrue(video_path.exists(), f"Video file {video_path} does not exist.")

    def test_video_create_from_file(self):
        """
        Test if a video file can be created from a file and cleaned up afterwards.
        """
        _test_video_create_from_file(self)

    def test_initialize_video_specs(self):
        """
        Test if the video file can be initialized with the correct specifications.
        """
        if not RUN_VIDEO_TESTS:
            logger.warning("Skipping test_initialize_video_specs because RUN_VIDEO_TESTS is False.")
            return

        video_path = self.non_anonym_video_path
        video_file = None # Initialize video_file to None
        try:
            # Create the video file
            video_file = VideoFile.create_from_file(
                video_path,
                center_name=self.center.name, # Pass center name
                delete_source=False
            )
            self.assertIsNotNone(video_file, "VideoFile creation failed.")

            # Initialize specs
            initialized = video_file.initialize_video_specs(use_raw = True)
            self.assertTrue(initialized, "initialize_video_specs returned False.")

            # Refresh from DB to ensure fields were saved
            video_file.refresh_from_db()

            # Assertions
            self.assertEqual(video_file.fps, 50)
            self.assertEqual(video_file.width, 1920)
            self.assertEqual(video_file.height, 1080)
            # Duration might be slightly off due to float precision, check within a tolerance
            self.assertAlmostEqual(video_file.duration, 10.0, delta=0.1)
            # Frame count might also vary slightly depending on calculation method
            # self.assertEqual(video_file.frame_count, 500) # Example, adjust if needed

        finally:
            # Cleanup
            if video_file and video_file.pk:
                logger.info(f"Cleaning up video file from test_initialize_video_specs (UUID: {video_file.uuid})")
                try:
                    # Reload from DB before deleting
                    video_to_delete = VideoFile.objects.get(pk=video_file.pk)
                    video_to_delete.delete_with_file()
                    logger.info(f"Successfully cleaned up video file (UUID: {video_file.uuid})")
                except VideoFile.DoesNotExist:
                    logger.warning(f"VideoFile with pk {video_file.pk} not found for cleanup.")
                except Exception as e:
                    logger.error(f"Error during cleanup of video file (UUID: {video_file.uuid}): {e}", exc_info=True)

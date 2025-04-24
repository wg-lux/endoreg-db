import random
from .helper import get_random_video_path_by_examination_alias
from django.test import TestCase
from logging import getLogger
from ._video_create_from_file import _test_video_create_from_file
import unittest
import shutil

from endoreg_db.models import (
    VideoFile,
    Frame  # Import Frame model
)
import logging
from django.conf import settings

RUN_VIDEO_TESTS = settings.RUN_VIDEO_TESTS
assert isinstance(RUN_VIDEO_TESTS, bool), "RUN_VIDEO_TESTS must be a boolean value"


logger = getLogger("video_file")
logger.setLevel(logging.WARNING)

from ...helpers.default_objects import (
    get_default_center,
    get_default_processor,
)

from ...helpers.data_loader import (
    load_disease_data,
    load_gender_data,
    load_event_data,
    load_information_source,
    load_examination_data,
    load_center_data,
    load_endoscope_data,
    load_ai_model_label_data,
    load_ai_model_data,
)

# Check for ffmpeg executable once
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
if not FFMPEG_AVAILABLE:
    logger.warning("ffmpeg command not found. Frame extraction tests will be skipped.")


def _test_extract_text_meta(test:"VideoFileModelExtractedTest"):
    """
    Test if the video file can be initialized with the correct specifications.
    """
    video_file = test.video_file
    if not RUN_VIDEO_TESTS:
        logger.warning("Skipping test_initialize_video_specs because RUN_VIDEO_TESTS is False.")
        return

    video_file.refresh_from_db()

    # Check if the video file has been initialized
    video_file.update_text_metadata()

def _test_frame_paths(test:"VideoFileModelExtractedTest"):
    """
    test the VideoFile Objects methods get_frame_path and get_frame_paths
    """
    video_file = test.video_file
    if not RUN_VIDEO_TESTS:
        logger.warning("Skipping test_initialize_video_specs because RUN_VIDEO_TESTS is False.")
        return

    video_file.refresh_from_db()

    # Check if the 5 random frame paths correct
    frame_count = video_file.frame_count
    frame_paths = video_file.get_frame_paths()
    test.assertEqual(len(frame_paths), frame_count, "Number of frame paths should match the number of frames.")
    # check 5 random frame paths
    selected_frame_paths = random.sample(frame_paths, 5)
    for i, frame_path in enumerate(selected_frame_paths):
        test.assertTrue(frame_path.exists(), f"Frame path {frame_path} should exist.")
    
    # get 5 random integers between 0 and frame_count
    random_frame_indices = random.sample(range(frame_count), 5)
    for i in random_frame_indices:
        frame_path = video_file.get_frame_path(i)
        test.assertTrue(frame_path.exists(), f"Frame path {frame_path} should exist.")
    
    # Check if first and last frame exist
    first_frame_path = video_file.get_frame_path(0) # Get path for frame 0
    last_frame_path = video_file.get_frame_path(frame_count - 1) # Get path for last frame
    test.assertTrue(first_frame_path.exists(), f"First frame file {first_frame_path} should exist.")
    test.assertTrue(last_frame_path.exists(), f"Last frame file {last_frame_path} should exist.")

    

class VideoFileModelExtractedTest(TestCase):
    def setUp(self):
        load_gender_data()
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
        self.endo_processor = get_default_processor()

        self.video_file = VideoFile.create_from_file(
                self.non_anonym_video_path,
                center_name=self.center.name, # Pass center name
                delete_source=False,
                processor_name = self.endo_processor.name, # Pass processor name
            )
        
        _initialized = self.video_file.initialize_video_specs(use_raw = True)
        self.frame_paths = self.video_file.extract_frames()

    @unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg command not found, skipping frame extraction test.")
    def test_frames_extracted(self):
        """
        Test if the frames can be extracted from the video file.
        Requires FFmpeg to be installed and in PATH.
        """
        video_file = self.video_file
        video_file.refresh_from_db() # Reload to get latest state
        frame_paths = self.frame_paths

        self.assertIsNotNone(frame_paths, "extract_frames should return a list, not None.")
        self.assertGreater(len(frame_paths), 0, "Expected to extract at least one frame.")
        # Check if the number of extracted frames matches the expected count
        self.assertEqual(len(frame_paths), video_file.frame_count, "Number of extracted frames should match video's frame count.")

        # Check state
        self.assertTrue(video_file.state.frames_extracted, "VideoState.frames_extracted should be True after extraction.")
        self.assertTrue(video_file.state.frames_initialized, "VideoState.frames_initialized should be True after initialization.")

        # Check Frame objects in DB using direct query
        frame_count_db = Frame.objects.filter(video=video_file).count()
        self.assertEqual(frame_count_db, video_file.frame_count, "Number of Frame objects in DB should match frame count.")

        # Check if frame files exist (check first and last)
        first_frame_path = video_file.get_frame_path(0) # Get path for frame 0
        last_frame_path = video_file.get_frame_path(video_file.frame_count - 1) # Get path for last frame
        self.assertTrue(first_frame_path.exists(), f"First frame file {first_frame_path} should exist.")
        self.assertTrue(last_frame_path.exists(), f"Last frame file {last_frame_path} should exist.")

        _test_extract_text_meta(self)
        _test_frame_paths(self)


    def tearDown(self):

        self.video_file.delete_with_file()
        return super().tearDown()

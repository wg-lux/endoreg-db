import os
from .helper import get_random_video_path_by_examination_alias
from django.test import TestCase
from logging import getLogger

from endoreg_db.models import (
    VideoFile,
)
from endoreg_db.models.label import LabelType, LabelSet

from django.conf import settings

RUN_VIDEO_TESTS = settings.RUN_VIDEO_TESTS
assert isinstance(RUN_VIDEO_TESTS, bool), "RUN_VIDEO_TESTS must be a boolean value"


logger = getLogger(__name__)
logger.debug("Starting test for Patient model")

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

        self.non_anonym_video = get_random_video_path_by_examination_alias(is_anonymous=False)

    def test_random_video_exists(self):
        """
        Test if all video files exist.
        """
        video_file = VideoFile.objects.first()
        self.assertIsInstance(video_file, VideoFile)
        self.assertTrue(os.path.exists(self.random_video_path), f"Video file {self.random_video_path} does not exist.")
        
        
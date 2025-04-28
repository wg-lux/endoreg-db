
from .mock_video_anonym_annotation import mock_video_anonym_annotation
from .test_pipe_2 import _test_pipe_2
from .test_pipe_1 import _test_pipe_1

from django.test import TransactionTestCase
from logging import getLogger
import unittest
import shutil

from endoreg_db.models import (
    ModelMeta # Import VideoState # Import SensitiveMeta, # Import ,
)
import logging
from django.conf import settings

RUN_VIDEO_TESTS = settings.RUN_VIDEO_TESTS
assert isinstance(RUN_VIDEO_TESTS, bool), "RUN_VIDEO_TESTS must be a boolean value"


logger = getLogger("video_file")
logger.setLevel(logging.WARNING)

from ...helpers.default_objects import (
    get_default_video_file,
    get_latest_segmentation_model
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
    load_default_ai_model
)

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
if not FFMPEG_AVAILABLE:
    logger.warning("ffmpeg command not found. Frame extraction tests will be skipped.")


class VideoFileModelExtractedTest(TransactionTestCase):
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
        load_default_ai_model()


        # Fetch the associated ModelMeta instance
        try:
            # Assuming ModelMeta has a foreign key 'model' to AiModel
            self.ai_model_meta = get_latest_segmentation_model()
        except ModelMeta.DoesNotExist as exc:
            raise AssertionError("No ModelMeta found for the latest default segmentation AiModel") from exc
    


        self.video_file = get_default_video_file()

        self.center = self.video_file.center
        self.endo_processor = self.video_file.processor

    @unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg command not found, skipping frame extraction test.")
    def test_pipeline(self):
        """
        Test the pipeline of the video file.
        This includes:
        - Pre-validation processing (pipe_1)
        - Simulating human validation processing (test_after_pipe_1)
        - Post-validation processing (pipe_2)
        """
        _test_pipe_1(self)
        mock_video_anonym_annotation(self)
        _test_pipe_2(self)

    def tearDown(self):

        self.video_file.delete_with_file()
        return super().tearDown()

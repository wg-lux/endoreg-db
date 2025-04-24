import random

from tests.media import video
from .mock_video_anonym_annotation import mock_video_anonym_annotation
from .test_pipe_2 import _test_pipe_2
from .test_pipe_1 import _test_pipe_1

from .helper import get_random_video_path_by_examination_alias
from django.test import TestCase, TransactionTestCase
from logging import getLogger
import unittest
import shutil

from endoreg_db.models import (
    VideoFile,
    Frame,
    ModelMeta,
    AiModel,
    VideoState # Import VideoState # Import SensitiveMeta, # Import ,
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
    get_latest_segmentation_model
    # get_default_model,
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

        # Assume get_latest_segmentation_model returns an AiModel instance
        ai_model_instance: AiModel = get_latest_segmentation_model()

        # Fetch the associated ModelMeta instance
        try:
            # Assuming ModelMeta has a foreign key 'model' to AiModel
            self.ai_model_meta = ModelMeta.objects.get(model=ai_model_instance)
        except ModelMeta.DoesNotExist as exc:
            raise AssertionError(f"No ModelMeta found for the latest segmentation AiModel: {ai_model_instance}") from exc
        except ModelMeta.MultipleObjectsReturned:
            # If multiple exist, perhaps get the latest one based on version or date?
            # For now, assume a single ModelMeta per AiModel or handle ambiguity.
            self.ai_model_meta = ModelMeta.objects.filter(model=ai_model_instance).latest('version')  # Example: get latest version
            logger.warning(f"Multiple ModelMeta found for AiModel {ai_model_instance}. Using the latest version: {self.ai_model_meta.version}")
            # Alternatively, raise AssertionError if this case is unexpected.
            # raise AssertionError(f"Multiple ModelMeta found for AiModel: {ai_model_instance}")

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

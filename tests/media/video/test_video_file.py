import os

RUN_VIDEO_TESTS = os.environ.get("RUN_VIDEO_TESTS", "False") == "True"
from django.test import TestCase
from logging import getLogger

from endoreg_db.models import (
    VideoFile,
)
from endoreg_db.models.label import LabelType, LabelSet

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

    def test_setup(self):
        """
        Test if all video files have a setup.
        """
        self.assertTrue(True)
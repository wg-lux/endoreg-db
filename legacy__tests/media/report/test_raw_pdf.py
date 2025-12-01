from django.test import TestCase
from logging import getLogger

from endoreg_db.models import (
    RawPdfFile  # Import Frame model
)
import logging
from django.conf import settings

from endoreg_db.utils.video.ffmpeg_wrapper import is_ffmpeg_available

RUN_VIDEO_TESTS = settings.RUN_VIDEO_TESTS
assert isinstance(RUN_VIDEO_TESTS, bool), "RUN_VIDEO_TESTS must be a boolean value"


logger = getLogger(__name__)
logger.setLevel(logging.WARNING)

from ...helpers.default_objects import get_default_egd_pdf

from ...helpers.data_loader import (
    load_disease_data,
    load_event_data,
    load_gender_data,
    load_information_source_data,
    load_examination_data,
    load_center_data,
    load_endoscope_data,
)

FFMPEG_AVAILABLE = is_ffmpeg_available()


class RawPdfFileModelTest(TestCase):
    def setUp(self):  
        load_gender_data()
        load_disease_data()
        load_event_data()
        load_information_source_data()
        load_examination_data()
        load_center_data()
        load_endoscope_data()

        # Create and process the RawPdfFile, which includes creating SensitiveMeta
        created_pdf_file = get_default_egd_pdf()
        # Re-fetch the instance from the database to ensure all relations are current
        self.raw_pdf_file = RawPdfFile.objects.get(pk=created_pdf_file.pk)

    def test_raw_pdf_file_creation(self):
        self.assertIsInstance(self.raw_pdf_file, RawPdfFile)
        logger.warning(f"Raw PDF file created: {self.raw_pdf_file.file.name}")
        
        raw_pdf_file = self.raw_pdf_file
        center = raw_pdf_file.center
        text = raw_pdf_file.text
        sensitive_meta  = raw_pdf_file.sensitive_meta


        self.assertIsNotNone(sensitive_meta, "Sensitive Meta should not be None")
        logger.warning(f"Sensitive Meta: {sensitive_meta}")
        
        self.assertIsNotNone(center, "Center should not be None")
        logger.warning(f"Center: {center}")

        # self.assertIsNotNone(examination, "Examination should not be None")
        logger.warning(f"Text: {text}")

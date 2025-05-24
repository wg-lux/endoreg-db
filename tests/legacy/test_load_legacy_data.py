from django.test import TransactionTestCase
from logging import getLogger
import shutil
from typing import List
# from endoreg_db.models import (
#)
import logging
from django.conf import settings
import json

logger = getLogger("legacy_data")
logger.setLevel(logging.WARNING)

from ..helpers.data_loader import (
    load_ai_model_label_data,
    load_ai_model_data,
)

IMG_DICT_PATH = "tests/assets/legacy_img_dicts.jsonl"



FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
if not FFMPEG_AVAILABLE:
    logger.warning("ffmpeg command not found. Frame extraction tests will be skipped.")


class LegacyImageDataTest(TransactionTestCase):
    img_dicts: List[dict]
    def setUp(self):
        """
        Prepares test data by loading AI model data and parsing legacy image dictionaries.
        
        Loads AI model label and model data, then reads and parses a JSON Lines file of legacy image dictionaries into the `img_dicts` attribute for use in tests.
        """
        load_ai_model_label_data()
        load_ai_model_data()

        # read the .jsonl file
        with open(IMG_DICT_PATH, "r", encoding = "utf-8") as f:
            self.img_dicts = [json.loads(line) for line in f]


    def test_load_legacy_data(self):
        """
        Verifies that legacy image dictionaries are loaded from the JSONL file.
        
        Asserts that the list of image dictionaries is not empty, confirming successful data loading during test setup.
        """
        assert len(self.img_dicts) > 0, "No image dictionaries found in the JSONL file."


    def tearDown(self):
        pass
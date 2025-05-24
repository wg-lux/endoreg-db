from django.test import TransactionTestCase
from logging import getLogger
from typing import List
# from endoreg_db.models import (
#)
import logging
import json
from endoreg_db.utils.video.ffmpeg_wrapper import is_ffmpeg_available # ADDED

logger = getLogger("legacy_data")
logger.setLevel(logging.WARNING)

from ..helpers.data_loader import (
    load_ai_model_label_data,
    load_ai_model_data,
)

IMG_DICT_PATH = "tests/assets/legacy_img_dicts.jsonl"

FFMPEG_AVAILABLE = is_ffmpeg_available() # ADDED



class LegacyImageDataTest(TransactionTestCase):
    img_dicts: List[dict]
    def setUp(self):
        load_ai_model_label_data()
        load_ai_model_data()

        # read the .jsonl file
        with open(IMG_DICT_PATH, "r", encoding = "utf-8") as f:
            self.img_dicts = [json.loads(line) for line in f]


    def test_load_legacy_data(self):
        """
        """
        assert len(self.img_dicts) > 0, "No image dictionaries found in the JSONL file."


    def tearDown(self):
        pass
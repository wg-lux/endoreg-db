from django.test import TestCase
import os
from endoreg_db.utils.pipelines.process_video_dir import process_video_dir
from ..media.video.helper import get_random_video_path_by_examination_alias
from logging import getLogger
from ..helpers.data_loader import load_base_db_data, load_default_ai_model

logger = getLogger(__name__)

SKIP_EXPENSIVE_PIPELINE = os.environ.get("SKIP_EXPENSIVE_PIPELINE", "true").lower() == "true"

class ProcessVideoDirTest(TestCase):
    def setUp(self):
        # Load the base data into the database
        video_file_path = get_random_video_path_by_examination_alias("egd")
        self.video_dir = video_file_path.parent
        load_base_db_data()
        load_default_ai_model()
        

    def test_process_video_dir(self):
        """
        Test if the process_video_dir function runs without errors.
        """
        if SKIP_EXPENSIVE_PIPELINE:
            logger.warning("Skipping expensive pipeline tests.")
            return
        try:
            process_video_dir(video_dir=self.video_dir)
        except Exception as e:
            self.fail(f"process_video_dir command failed: {e}")

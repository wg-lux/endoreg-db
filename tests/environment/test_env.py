from django.test import TestCase
import os
from pathlib import Path
from endoreg_db.utils.paths import STORAGE_DIR
import logging

logger = logging.getLogger(__name__)

class TestEnvironment(TestCase):
    """
    Test the environment setup for the project.
    This includes checking if the necessary environment variables are set.
    """

    def test_storage_dir(self):
        """
        Test if the STORAGE_DIR environment variable is set correctly.
        """
        storage_dir = os.environ.get("STORAGE_DIR")

        self.assertEqual(storage_dir, "data/tests/storage", 
                         "STORAGE_DIR environment variable is not set correctly.")

        storage_dir_path = Path(storage_dir).resolve().as_posix()
        util_storage_dir_path = STORAGE_DIR.resolve().as_posix()
        logger.warning(f"STORAGE_DIR: {storage_dir_path}")
        logger.warning(f"STORAGE_DIR from utils: {util_storage_dir_path}")
        self.assertEqual(storage_dir_path, util_storage_dir_path,
                         "STORAGE_DIR path does not match the expected path.")
        
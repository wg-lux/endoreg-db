import os
from pathlib import Path
from django.test import TestCase
from endoreg_db.utils.paths import IO_DIR, PROTECTED_DATA_ROOT, STORAGE_DIR
import logging
import importlib

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
        protected_root = os.environ.get("LX_ANNOTATE_ENCRYPTED_DATA_DIR")
        io_dir = os.environ.get("IO_DIR")

        self.assertIsNotNone(storage_dir)
        self.assertIsNotNone(protected_root)
        self.assertIsNotNone(io_dir)

        assert storage_dir is not None
        assert protected_root is not None
        assert io_dir is not None
        storage_dir_path = Path(storage_dir).resolve().as_posix()
        util_storage_dir_path = STORAGE_DIR.resolve().as_posix()
        protected_root_path = Path(protected_root).resolve().as_posix()
        util_protected_root_path = PROTECTED_DATA_ROOT.resolve().as_posix()
        io_dir_path = Path(io_dir).resolve().as_posix()
        util_io_dir_path = IO_DIR.resolve().as_posix()
        logger.warning(f"STORAGE_DIR: {storage_dir_path}")
        logger.warning(f"STORAGE_DIR from utils: {util_storage_dir_path}")
        self.assertEqual(
            storage_dir_path,
            util_storage_dir_path,
            "STORAGE_DIR path does not match the expected path.",
        )
        self.assertEqual(
            protected_root_path,
            util_protected_root_path,
            "Protected root path does not match the expected path.",
        )
        self.assertEqual(
            io_dir_path,
            util_io_dir_path,
            "IO_DIR path does not match the expected path.",
        )
        self.assertTrue(
            storage_dir_path.startswith(protected_root_path),
            "STORAGE_DIR must resolve within the protected root.",
        )
        self.assertEqual(
            io_dir_path,
            protected_root_path,
            "IO_DIR should resolve to the protected root used in tests.",
        )

    def test_core_modules_import(self):
        modules = [
            "endoreg_db.models",
            "endoreg_db.serializers",
            "endoreg_db.views",
            "endoreg_db.utils.paths",
        ]
        for module_name in modules:
            imported = importlib.import_module(module_name)
            assert imported is not None

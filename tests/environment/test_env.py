from django.test import TestCase
import os
from pathlib import Path
from endoreg_db.utils.paths import IO_DIR, PROTECTED_DATA_ROOT, STORAGE_DIR
import logging
import importlib
import pkgutil
import endoreg_db

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

        self.assertEqual(
            storage_dir,
            "data/tests/protected_runtime/storage",
            "STORAGE_DIR environment variable is not set correctly.",
        )
        self.assertEqual(
            protected_root,
            "data/tests/protected_runtime",
            "LX_ANNOTATE_ENCRYPTED_DATA_DIR is not set correctly.",
        )
        self.assertEqual(
            io_dir,
            "data/tests/protected_runtime",
            "IO_DIR environment variable is not set correctly.",
        )

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

    def test_all_imports(self):
        errors = []
        for module in pkgutil.walk_packages(
            endoreg_db.__path__, endoreg_db.__name__ + "."
        ):
            name = module.name
            try:
                importlib.import_module(name)
            except Exception as exc:
                errors.append((name, exc))

        for name, exc in errors:
            print(f"{name}: {exc}")

        assert len(errors) == 0

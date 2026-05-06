import importlib
import logging
import os
from pathlib import Path

from django.test import TestCase

from endoreg_db.config import env as env_module

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
        data_dir = os.environ.get("DATA_DIR")

        self.assertIsNotNone(storage_dir)
        self.assertIsNotNone(protected_root)
        self.assertIsNotNone(data_dir)

        assert storage_dir is not None
        assert protected_root is not None
        assert data_dir is not None
        storage_dir_path = Path(storage_dir).resolve().as_posix()
        protected_root_path = Path(protected_root).resolve().as_posix()
        data_dir_path = Path(data_dir).resolve().as_posix()

        self.assertTrue(
            storage_dir_path.startswith(protected_root_path),
            "STORAGE_DIR must resolve within the protected root.",
        )
        self.assertFalse(
            data_dir_path.startswith(protected_root_path),
            "DATA_DIR should remain separate from the protected root used in tests.",
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

    def test_env_helpers_define_base_runtime_defaults(self):
        original_user_rate = os.environ.pop("DRF_THROTTLE_USER", None)
        original_anon_rate = os.environ.pop("DRF_THROTTLE_ANON", None)
        try:
            self.assertEqual(
                env_module.get_asset_dir(),
                env_module.BASE_DIR / "tests/assets",
            )
            self.assertEqual(env_module.get_time_zone(), "Europe/Berlin")
            self.assertEqual(env_module.get_static_url(), "/static/")
            self.assertEqual(env_module.get_protected_media_url(), "/protected_media/")
            self.assertEqual(
                env_module.get_protected_media_root(),
                Path(os.environ["PROTECTED_MEDIA_ROOT"]).resolve(),
            )
            self.assertEqual(
                env_module.get_media_root(),
                env_module.get_protected_media_root(),
            )
            self.assertEqual(env_module.get_cache_timeout_seconds(), 60 * 30)
            self.assertEqual(env_module.get_drf_throttle_user_rate(), "100/hour")
            self.assertEqual(env_module.get_drf_throttle_anon_rate(), "20/hour")
        finally:
            if original_user_rate is not None:
                os.environ["DRF_THROTTLE_USER"] = original_user_rate
            if original_anon_rate is not None:
                os.environ["DRF_THROTTLE_ANON"] = original_anon_rate

    def test_endoreg_deployment_role_validation(self):
        original = os.environ.get("ENDOREG_DEPLOYMENT_ROLE")
        try:
            os.environ["ENDOREG_DEPLOYMENT_ROLE"] = "central_hub"
            self.assertEqual(
                env_module.get_endoreg_deployment_role(),
                "central_hub",
            )
            os.environ["ENDOREG_DEPLOYMENT_ROLE"] = "local_study_server"
            self.assertEqual(
                env_module.get_endoreg_deployment_role(),
                "local_study_server",
            )

            os.environ["ENDOREG_DEPLOYMENT_ROLE"] = "invalid-role"
            with self.assertRaises(ValueError):
                env_module.get_endoreg_deployment_role()
        finally:
            if original is None:
                os.environ.pop("ENDOREG_DEPLOYMENT_ROLE", None)
            else:
                os.environ["ENDOREG_DEPLOYMENT_ROLE"] = original

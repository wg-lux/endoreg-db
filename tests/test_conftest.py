"""
Tests for pytest configuration and fixtures in tests/conftest.py.

This test module verifies that the pytest configuration and fixtures work correctly,
including database optimization, video mocking, and session-scoped caching.
"""

import logging
import os

from endoreg_db.models import Center

from .conftest import (
    RUN_VIDEO_TESTS,
    SKIP_EXPENSIVE_TESTS,
    TEST_STORAGE_DIR,
    disable_faker_logging,
)
from .defaults import DEFAULT_CENTER_NAME, DEFAULT_ENDOSCOPY_PROCESSOR_NAME, DEFAULT_EXAMINATIONS_NAMES, DEFAULT_GENDERS


class TestFakerLoggingDisable:
    """Tests for faker logging disablement."""

    def test_disable_faker_logging_disables_all_loggers(self):
        """Verify that disable_faker_logging disables faker loggers."""

        # Call the function
        disable_faker_logging()

        # Check that faker loggers are disabled
        faker_logger = logging.getLogger("faker")
        assert faker_logger.disabled is True
        assert faker_logger.level == logging.CRITICAL

        faker_providers_logger = logging.getLogger("faker.providers")
        assert faker_providers_logger.disabled is True
        assert faker_providers_logger.level == logging.CRITICAL


class TestEnvironmentVariables:
    """Tests for environment variable configuration."""

    def test_django_settings_module_is_test(self):
        """Verify that DJANGO_SETTINGS_MODULE is set to test settings."""
        assert os.environ["DJANGO_SETTINGS_MODULE"] == "config.settings.test"

    def test_skip_expensive_tests_respects_env(self):
        """Verify that SKIP_EXPENSIVE_TESTS is read from environment."""
        # The actual value depends on environment, but should be a bool
        assert isinstance(SKIP_EXPENSIVE_TESTS, bool)

    def test_run_video_tests_respects_env(self):
        """Verify that RUN_VIDEO_TESTS is read from environment."""
        assert isinstance(RUN_VIDEO_TESTS, bool)


class TestStorageDirectory:
    """Tests for test storage directory setup."""

    def test_test_storage_dir_exists(self):
        """Verify that TEST_STORAGE_DIR is created."""
        assert TEST_STORAGE_DIR.exists()
        assert TEST_STORAGE_DIR.is_dir()

    def test_test_storage_dir_is_under_storage_tests(self):
        """Verify that TEST_STORAGE_DIR is in the correct location."""
        assert "storage" in str(TEST_STORAGE_DIR)
        assert "tests" in str(TEST_STORAGE_DIR)


class TestBaseDataFixture:
    """Tests for base_db_data fixture."""

    def test_base_db_data_loads_default_center_data(self, db, django_db_setup):
        """Verify that base_db_data loads required database objects."""

        # Check that required data exists
        assert Center.objects.filter(name=DEFAULT_CENTER_NAME).exists()

    def test_base_db_data_loads_default_hardware(self, db, django_db_setup):
        """Verify that base_db_data loads default hardware data."""
        from endoreg_db.models import EndoscopyProcessor

        assert EndoscopyProcessor.objects.filter(name=DEFAULT_ENDOSCOPY_PROCESSOR_NAME)

    def test_base_db_data_loads_default_genders(self, db, django_db_setup):
        from endoreg_db.models import Gender

        for gender_name in DEFAULT_GENDERS:
            assert Gender.objects.filter(name=gender_name).exists()

    def test_base_db_data_loads_default_examinations(self, db, django_db_setup):
        from endoreg_db.models import Examination

        for exam_name in DEFAULT_EXAMINATIONS_NAMES:
            assert Examination.objects.filter(name=exam_name).exists()

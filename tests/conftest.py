"""
pytest configuration for Django tests.

This file configures pytest-django and sets up test fixtures and configurations.
"""

import os
import sys
from pathlib import Path
import logging

import pytest
from django.test import override_settings

# Disable faker logging immediately on import
def disable_faker_logging():
    """Completely disable faker logging"""
    faker_logger = logging.getLogger('faker')
    faker_logger.disabled = True
    faker_logger.setLevel(logging.CRITICAL)
    
    # Also disable faker.providers which can be very noisy
    faker_providers_logger = logging.getLogger('faker.providers')
    faker_providers_logger.disabled = True
    faker_providers_logger.setLevel(logging.CRITICAL)
    
    # Disable any other faker-related loggers
    for logger_name in ['faker.factory', 'faker.generator']:
        logger = logging.getLogger(logger_name)
        logger.disabled = True
        logger.setLevel(logging.CRITICAL)

# Call this immediately to suppress faker logging
disable_faker_logging()

# Ensure the project root is in the Python path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Configure pytest-django to use our test settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings_test")

# Set up storage directory for tests
TEST_STORAGE_DIR = Path(__file__).parent.parent / "storage" / "tests"
TEST_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

@pytest.fixture(scope="session")
def django_db_setup():
    """
    Set up the test database for the session.
    Since we're using in-memory SQLite, this is minimal.
    """
    pass

@pytest.fixture(autouse=True)
def enable_db_access_for_all_tests(db):
    """
    Allow database access for all tests.
    This fixture is automatically used for all tests.
    """
    pass

@pytest.fixture
def api_client():
    """
    Provide a DRF API client for testing API endpoints.
    """
    from rest_framework.test import APIClient
    return APIClient()

@pytest.fixture
def test_settings():
    """
    Provide test-specific settings overrides.
    """
    return override_settings(
        MEDIA_ROOT=TEST_STORAGE_DIR,
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )

@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """
    Set up the test environment once per session.
    """
    # Ensure faker logging is disabled
    disable_faker_logging()
    
    # Ensure storage directories exist
    TEST_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    
    # Set environment variables for tests
    os.environ.setdefault("STORAGE_DIR", str(TEST_STORAGE_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings_test")
    
    yield
    
    # Cleanup after all tests
    import shutil
    if TEST_STORAGE_DIR.exists():
        shutil.rmtree(TEST_STORAGE_DIR, ignore_errors=True)
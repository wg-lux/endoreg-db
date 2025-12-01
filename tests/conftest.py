"""
pytest configuration for Django tests.

This file configures pytest-django and sets up test fixtures and configurations.
Includes session-scoped fixtures for video files and database optimization.
"""

import logging
import os
import shutil
import sys
from pathlib import Path

import pytest

# pytest_plugins = []

# Ensure the project root is in the Python path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Performance optimization settings
SKIP_EXPENSIVE_TESTS = os.environ.get("SKIP_EXPENSIVE_TESTS", "true").lower() == "true"
RUN_VIDEO_TESTS = os.environ.get("RUN_VIDEO_TESTS", "false").lower() == "false" and False or os.environ.get("RUN_VIDEO_TESTS", "false").lower() == "true"
USE_STUB_MODEL_META = os.environ.get("USE_STUB_MODEL_META", "true").lower() == "true"

# Set up storage directory for tests
TEST_STORAGE_DIR = Path(__file__).parent.parent / "storage" / "tests"
TEST_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

from django.conf import settings

# IMPORT FIXTURES
from .defaults.fixtures import colonoscopy_examination, django_db_setup, new_demo_finding

asset_dir = Path(getattr(settings, "ASSET_DIR", settings.BASE_DIR / "tests" / "assets"))


# Disable faker logging immediately on import
def disable_faker_logging():
    """Completely disable faker logging"""
    faker_logger = logging.getLogger("faker")
    faker_logger.disabled = True
    faker_logger.setLevel(logging.CRITICAL)

    # Also disable faker.providers which can be very noisy
    faker_providers_logger = logging.getLogger("faker.providers")
    faker_providers_logger.disabled = True
    faker_providers_logger.setLevel(logging.CRITICAL)

    # Disable any other faker-related loggers
    for logger_name in ["faker.factory", "faker.generator"]:
        logger = logging.getLogger(logger_name)
        logger.disabled = True
        logger.setLevel(logging.CRITICAL)


# Call this immediately to suppress faker logging
disable_faker_logging()


@pytest.fixture
def video_asset_path() -> Path:
    """Return a representative test video asset bundled with the test suite."""
    preferred = asset_dir / "test_endoscope.mp4"
    if preferred.exists():
        return preferred

    candidates = sorted(asset_dir.glob("*.mp4"))
    if not candidates:
        pytest.skip("No MP4 test assets available")
    return candidates[0]


@pytest.fixture
def video_asset_file(tmp_path, video_asset_path: Path) -> Path:
    """Provide a writable copy of the default video asset for file-operation tests."""
    target = tmp_path / video_asset_path.name
    shutil.copy2(video_asset_path, target)
    return target

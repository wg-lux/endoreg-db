"""Configure process-isolated test paths before pytest-django initializes."""

from __future__ import annotations

import os
from pathlib import Path

from tests.runtime_paths import build_test_run_namespace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKER_ID = os.environ.get("PYTEST_XDIST_WORKER")
RUN_NAMESPACE = build_test_run_namespace(WORKER_ID, os.getpid())
RUN_ROOT = PROJECT_ROOT / "data" / "tests" / "workers" / RUN_NAMESPACE
PROTECTED_ROOT = RUN_ROOT / "protected_runtime"
STORAGE_ROOT = PROTECTED_ROOT / "storage"
STREAMABLE_ROOT = STORAGE_ROOT / "streamable_videos"

os.environ["ENDOREG_TEST_RUN_NAMESPACE"] = RUN_NAMESPACE
os.environ["LX_ANNOTATE_ENCRYPTED_DATA_DIR"] = str(PROTECTED_ROOT)
os.environ["STORAGE_DIR"] = str(STORAGE_ROOT)
os.environ["DATA_DIR"] = str(RUN_ROOT / "runtime")
os.environ["PROTECTED_MEDIA_ROOT"] = str(STORAGE_ROOT)
os.environ["LX_ANNOTATE_STREAMABLE_VIDEO_ROOT"] = str(STREAMABLE_ROOT)
os.environ["LX_ANNOTATE_STREAMABLE_VIDEO_RAW_ROOT"] = str(STREAMABLE_ROOT / "raw")
os.environ["LX_ANNOTATE_STREAMABLE_VIDEO_PROCESSED_ROOT"] = str(
    STREAMABLE_ROOT / "processed"
)

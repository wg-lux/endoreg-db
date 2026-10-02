from __future__ import annotations

from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVENV_NIX = REPO_ROOT / "devenv.nix"


def _task_body(source: str, task_name: str) -> str:
    match = re.search(
        rf'"{re.escape(task_name)}"\s*=\s*\{{(?P<body>.*?)\n\s*\}};',
        source,
        flags=re.DOTALL,
    )
    assert match is not None, f"devenv task is missing: {task_name}"
    return match.group("body")


def _multiline_binding(source: str, binding_name: str) -> str:
    match = re.search(
        rf"{re.escape(binding_name)}\s*=\s*''(?P<body>.*?)'';",
        source,
        flags=re.DOTALL,
    )
    assert match is not None, f"devenv binding is missing: {binding_name}"
    return match.group("body")


def test_quality_regression_uses_synced_venv_without_nested_task_sync() -> None:
    source = DEVENV_NIX.read_text(encoding="utf-8")
    body = _task_body(source, "quality:code-regression")

    assert "devenv tasks run test:sync" not in body
    assert "devenv tasks run test:fast" not in body
    assert ".devenv/state/venv/bin/pyright" in body
    assert "scripts/check_dead_code.py" in body
    assert "scripts/check_quality_boundaries.py" in body
    assert "${FAST_TEST_ENV}" in body
    assert ".devenv/state/venv/bin/pytest -q ${FAST_TEST_PYTEST_ARGS}" in body


def test_standalone_fast_lane_retains_sync_and_shared_contract() -> None:
    source = DEVENV_NIX.read_text(encoding="utf-8")
    body = _task_body(source, "test:fast")

    assert "devenv tasks run test:sync" in body
    assert "${FAST_TEST_ENV}" in body
    assert "${FAST_TEST_PYTEST_ARGS}" in body
    assert "pytest -q ${FAST_TEST_PYTEST_ARGS}" in body
    assert "log_cli=true" not in body


def test_fast_lane_contract_keeps_required_markers_and_environment() -> None:
    source = DEVENV_NIX.read_text(encoding="utf-8")
    fast_test_environment = _multiline_binding(source, "FAST_TEST_ENV")

    assert (
        'FAST_TEST_MARKER = "not (expensive or video or pipeline or ai or slow or ffmpeg)";'
        in source
    )
    for assignment in (
        "export SKIP_EXPENSIVE_TESTS=true",
        "export RUN_VIDEO_TESTS=false",
        "export USE_STUB_MODEL_META=true",
        "export TEST_DB_REUSE=true",
    ):
        assert fast_test_environment.count(assignment) == 1
    assert (
        "FAST_TEST_PYTEST_ARGS = \"-m '${FAST_TEST_MARKER}' ${TEST_PYTEST_ARGS}\";"
        in source
    )


@pytest.mark.parametrize("task_name", ["test:heavy", "test:full"])
def test_other_lanes_keep_quiet_output_and_duration_reporting(task_name: str) -> None:
    source = DEVENV_NIX.read_text(encoding="utf-8")
    body = _task_body(source, task_name)

    assert "pytest -q" in body
    assert "${TEST_PYTEST_ARGS}" in body
    assert "log_cli=true" not in body
    assert 'TEST_PYTEST_ARGS = "-n auto --dist=loadscope --durations=30";' in source

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
    assert ".devenv/state/venv/bin/pytest -q ${FAST_TEST_PYTEST_ARGS}" in body
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

    assert ".devenv/state/venv/bin/pytest -q" in body
    assert "${TEST_PYTEST_ARGS}" in body
    assert "log_cli=true" not in body
    assert 'TEST_PYTEST_ARGS = "-n auto --dist=loadscope --durations=30";' in source


def test_shell_reload_cannot_recreate_or_resync_the_active_environment() -> None:
    source = DEVENV_NIX.read_text(encoding="utf-8")
    assert "venv.enable = false;" in source
    assert "sync.enable = false;" in source
    assert 'SYNC_CMD = "uv sync --python ${python}/bin/python --extra dev";' in source
    assert [
        line.strip()
        for line in _multiline_binding(source, "enterShell").strip().splitlines()
    ] == [
        'export VIRTUAL_ENV="$UV_PROJECT_ENVIRONMENT"',
        'export PATH="$VIRTUAL_ENV/bin:$PATH"',
    ]
    assert "${SYNC_CMD}" in _task_body(source, "agent:sync")
    assert "exec = SYNC_CMD;" in _task_body(source, "test:sync")


def test_default_commit_hooks_cannot_start_full_repository_checks() -> None:
    source = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    hooks = re.split(r"(?m)^      - id: ", source)[1:]
    default_hooks = {
        hook.splitlines()[0]: hook for hook in hooks if "stages: [manual]" not in hook
    }

    assert set(default_hooks) == {
        "ruff-check",
        "ruff-format",
        "uv-lock",
        "repository-structure",
    }
    for name in ("ruff-check", "ruff-format"):
        assert "pass_filenames: false" not in default_hooks[name]
        assert ".devenv/state/venv/bin/ruff" in default_hooks[name]
    for hook in default_hooks.values():
        assert "always_run: true" not in hook
        assert "files:" in hook
    assert "uv lock --check --offline" in default_hooks["uv-lock"]


def test_remote_ci_keeps_shared_gates_locked_dependencies_and_native_tests() -> None:
    source = DEVENV_NIX.read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    checks = _task_body(source, "ci:checks")

    assert "set -euo pipefail" in checks
    assert "--locked" in _task_body(source, "ci:sync")
    assert "uv sync" not in checks
    for guard in (
        "check_dead_code.py",
        "check_quality_boundaries.py",
        "check_lx_dtypes_contract_boundaries.py",
        "check_state_mutation_owners.py",
        "lint_structure.py",
        "check_django_startup_imports.py",
    ):
        assert guard in checks
    for task in ("ci:sync", "ci:checks", "ci:tests"):
        assert f"devenv tasks run {task}" in workflow
    assert workflow.index("ci:checks") < workflow.index("ci:tests")
    assert "scripts/run_rust_tests.sh --locked" in workflow
    assert "--junitxml=" in _task_body(source, "ci:tests")


def test_ci_actions_are_immutable_and_untrusted_code_has_no_write_permission() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    actions = re.findall(r"uses:\s+(\S+)", workflow)

    assert actions
    assert all(
        re.fullmatch(r"[\w-]+/[\w-]+@[0-9a-f]{40}", action) for action in actions
    )
    assert "contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert "pull_request_target" not in workflow
    assert "write-all" not in workflow
    assert "continue-on-error" not in workflow
    assert "timeout-minutes:" in workflow
    assert "cancel-in-progress: true" in workflow

from __future__ import annotations

import json
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from endoreg_db.management.commands import run_offline_batch as command_module
from endoreg_db.services.offline_batch_runner import (
    OfflineBatchAlreadyRunning,
    OfflineBatchRunResult,
    OfflineBatchRunnerConfig,
)


def test_command_emits_json_success_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = object()

    def load_config(_path: Path) -> object:
        return config

    def run_config(loaded: object) -> OfflineBatchRunResult:
        assert loaded is config
        return OfflineBatchRunResult(
            batch_id="runner-test-id",
            supervisor_config_sha256="a" * 64,
            workflow_config_sha256="b" * 64,
            started_at=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
            completed_at=datetime(2026, 7, 28, 10, 0, 1, 250000, tzinfo=UTC),
            status="completed",
            exit_code=0,
            duration_seconds=1.25,
            failure_count=0,
        )

    monkeypatch.setattr(
        command_module,
        "load_offline_batch_runner_config",
        load_config,
    )
    monkeypatch.setattr(
        command_module,
        "run_offline_batch",
        run_config,
    )
    stdout = StringIO()

    call_command(
        "run_offline_batch",
        "--config",
        "runner.yaml",
        "--json",
        stdout=stdout,
    )

    assert json.loads(stdout.getvalue()) == {
        "batch_id": "runner-test-id",
        "completed_at": "2026-07-28T10:00:01.250000+00:00",
        "duration_seconds": 1.25,
        "exit_code": 0,
        "failure_count": 0,
        "schema_version": "1.0",
        "started_at": "2026-07-28T10:00:00+00:00",
        "status": "completed",
        "supervisor_config_sha256": "a" * 64,
        "workflow_config_sha256": "b" * 64,
    }


def test_command_maps_lock_contention_to_temporary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = object()

    def load_config(_path: Path) -> object:
        return config

    monkeypatch.setattr(
        command_module,
        "load_offline_batch_runner_config",
        load_config,
    )

    def reject(_config: OfflineBatchRunnerConfig) -> OfflineBatchRunResult:
        raise OfflineBatchAlreadyRunning("already running")

    monkeypatch.setattr(command_module, "run_offline_batch", reject)

    with pytest.raises(CommandError) as exc_info:
        call_command(
            "run_offline_batch",
            "--config",
            Path("runner.yaml"),
            stdout=StringIO(),
            stderr=StringIO(),
        )

    assert exc_info.value.returncode == 75
    assert str(exc_info.value) == "already running"

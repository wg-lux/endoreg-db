from __future__ import annotations

# pyright: reportPrivateUsage=false

import json
import signal
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from endoreg_db.services import offline_batch_runner as runner
from endoreg_db.utils import rust_backend
from endoreg_db.utils.file_operations import advisory_file_lock


@pytest.fixture(autouse=True)
def stub_runtime_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[runner.OfflineBatchRunnerConfig], None]:
    original = runner._assert_runtime_readiness

    def accept(_config: runner.OfflineBatchRunnerConfig) -> None:
        return None

    monkeypatch.setattr(runner, "_assert_runtime_readiness", accept)
    return original


class _FakeProcess:
    def __init__(self, wait_result: int | None = 0) -> None:
        self.pid = 4242
        self.returncode: int | None = None
        self.wait_result = wait_result
        self.wait_calls = 0
        self.stop_signal: int | None = None
        self.on_first_wait: Callable[[], None] | None = None

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        if self.wait_calls == 1 and self.on_first_wait is not None:
            self.on_first_wait()
        if self.stop_signal is not None:
            self.returncode = -self.stop_signal
            return self.returncode
        if self.wait_result is None:
            raise subprocess.TimeoutExpired("snakemake", timeout or 0.0)
        self.returncode = self.wait_result
        return self.wait_result

    def terminate(self) -> None:
        self.stop_signal = signal.SIGTERM

    def kill(self) -> None:
        self.stop_signal = signal.SIGKILL


def _write_config(
    tmp_path: Path,
    *,
    resources: dict[str, int] | None = None,
    lock_path: str = "state/runner.lock",
    max_runtime_seconds: float = 10,
) -> Path:
    workflow_root = tmp_path / "workflow-root"
    workflow_root.mkdir()
    snakefile = workflow_root / "Snakefile"
    snakefile.write_text("rule all:\n    input: []\n", encoding="utf-8")
    workflow_config = workflow_root / "imports.yaml"
    workflow_config.write_text(
        yaml.safe_dump(
            {
                "django_settings_module": None,
                "receipt_directory": "receipts",
                "resources": {
                    "video": {
                        "threads": 4,
                        "mem_mb": 16000,
                        "rust_workers": 4,
                        "ffmpeg_threads": 4,
                        "gpu": 1,
                    },
                    "report": {
                        "threads": 1,
                        "mem_mb": 2048,
                        "rust_workers": 1,
                        "ffmpeg_threads": 1,
                        "gpu": 0,
                    },
                    "video_transcode": {
                        "threads": 4,
                        "mem_mb": 16000,
                        "rust_workers": 4,
                        "ffmpeg_threads": 4,
                        "gpu": 1,
                    },
                    "video_hls": {
                        "threads": 4,
                        "mem_mb": 16000,
                        "rust_workers": 4,
                        "ffmpeg_threads": 4,
                        "gpu": 1,
                    },
                },
                "video_imports": {
                    "video-job": {
                        "source": "source.mp4",
                        "center_name": "center",
                        "processor_name": "processor",
                    }
                },
                "report_imports": {
                    "report-job": {
                        "source": "report.pdf",
                        "center_name": "center",
                    }
                },
                "video_transcodes": {
                    "transcode-job": {
                        "video_id": 1,
                        "apply": True,
                    }
                },
                "video_hls_materializations": {
                    "hls-job": {
                        "video_id": 1,
                    }
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    profile = workflow_root / "profile"
    profile.mkdir()
    (profile / "config.yaml").write_text("printshellcmds: true\n", encoding="utf-8")
    config_path = tmp_path / "runner.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "workflow_root": "workflow-root",
                "snakefile": "workflow-root/Snakefile",
                "workflow_config": "workflow-root/imports.yaml",
                "profile": "workflow-root/profile",
                "lock_path": f"workflow-root/{lock_path}",
                "summary_directory": "workflow-root/state/summaries",
                "assert_environment_readiness": False,
                "required_native_capabilities": [
                    {
                        "name": "batch_file_identity",
                        "contract_version": "batch_file_identity_v1",
                    }
                ],
                "resources": resources
                or {
                    "cores": 4,
                    "mem_mb": 16000,
                    "gpu": 1,
                    "rust_workers": 4,
                },
                "lock_timeout_seconds": 0,
                "shutdown_grace_seconds": 0.01,
                "max_runtime_seconds": max_runtime_seconds,
                "heartbeat_seconds": 0.01,
                "poll_interval_seconds": 0.005,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return config_path


def _event_payloads(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == runner.__name__ and record.getMessage().startswith("{")
    ]


def test_load_config_resolves_reviewed_paths_and_builds_bounded_command(
    tmp_path: Path,
) -> None:
    config = runner.load_offline_batch_runner_config(_write_config(tmp_path))

    command = config.command(batch_id="test-batch")

    assert config.workflow_root == tmp_path / "workflow-root"
    assert config.lock_path == tmp_path / "workflow-root/state/runner.lock"
    assert command[1:3] == ("-m", "snakemake")
    assert command[-5:] == (
        "mem_mb=16000",
        "gpu=1",
        "rust_workers=4",
        "--config",
        "batch_id=test-batch",
    )


@pytest.mark.parametrize(
    ("resources", "message"),
    [
        (
            {"cores": 2, "mem_mb": 16000, "gpu": 1, "rust_workers": 3},
            "rust_workers cannot exceed",
        ),
        (
            {"cores": 0, "mem_mb": 16000, "gpu": 1, "rust_workers": 1},
            "Invalid offline batch runner configuration",
        ),
        (
            {"cores": 4, "mem_mb": 100, "gpu": 1, "rust_workers": 4},
            "cannot schedule configured video_import jobs",
        ),
        (
            {"cores": 4, "mem_mb": 16000, "gpu": 0, "rust_workers": 4},
            "cannot schedule configured video_import jobs",
        ),
    ],
)
def test_load_config_rejects_invalid_resource_budgets(
    tmp_path: Path,
    resources: dict[str, int],
    message: str,
) -> None:
    config_path = _write_config(tmp_path, resources=resources)

    with pytest.raises(runner.OfflineBatchConfigurationError, match=message):
        runner.load_offline_batch_runner_config(config_path)


def test_load_config_rejects_lock_outside_approved_workflow_root(
    tmp_path: Path,
) -> None:
    config_path = _write_config(tmp_path, lock_path="../../runner.lock")

    with pytest.raises(
        runner.OfflineBatchConfigurationError,
        match="lock_path must stay inside workflow_root",
    ):
        runner.load_offline_batch_runner_config(config_path)


def test_runner_emits_machine_readable_success_metrics(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = runner.load_offline_batch_runner_config(_write_config(tmp_path))
    process = _FakeProcess(wait_result=0)
    observed: list[tuple[tuple[str, ...], Path]] = []

    def start(command: tuple[str, ...], cwd: Path) -> _FakeProcess:
        observed.append((command, cwd))
        return process

    with caplog.at_level("INFO", logger=runner.__name__):
        result = runner.run_offline_batch(config, process_factory=start)

    assert result.exit_code == 0
    assert observed == [
        (config.command(batch_id=result.batch_id), config.workflow_root)
    ]
    summary_path = config.summary_directory / f"{result.batch_id}.json"
    persisted = runner.OfflineBatchRunSummary.model_validate_json(
        summary_path.read_text(encoding="utf-8")
    )
    assert persisted == result
    assert summary_path.stat().st_mode & 0o777 == 0o600
    assert config.summary_directory.stat().st_mode & 0o777 == 0o700
    payloads = _event_payloads(caplog)
    assert [payload["event"] for payload in payloads] == [
        "offline_batch.runner.lock_acquired",
        "offline_batch.runner.started",
        "offline_batch.runner.completed",
    ]
    assert payloads[-1]["metric_name"] == "offline_batch_completed_total"
    assert payloads[-1]["metric_value"] == 1
    assert "snakefile" not in json.dumps(payloads)


def test_runtime_readiness_rejection_prevents_process_start_and_persists_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = runner.load_offline_batch_runner_config(_write_config(tmp_path))
    started = False

    def reject(_config: runner.OfflineBatchRunnerConfig) -> None:
        raise runner.OfflineBatchConfigurationError("readiness rejected")

    def forbidden_start(
        _command: tuple[str, ...],
        _cwd: Path,
    ) -> _FakeProcess:
        nonlocal started
        started = True
        return _FakeProcess()

    monkeypatch.setattr(runner, "_assert_runtime_readiness", reject)

    with pytest.raises(runner.OfflineBatchConfigurationError):
        runner.run_offline_batch(config, process_factory=forbidden_start)

    assert started is False
    summaries = list(config.summary_directory.glob("*.json"))
    assert len(summaries) == 1
    persisted = runner.OfflineBatchRunSummary.model_validate_json(
        summaries[0].read_text(encoding="utf-8")
    )
    assert persisted.status == "failed"
    assert persisted.exit_code == 78


def test_missing_required_native_capability_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_runtime_readiness: Callable[[runner.OfflineBatchRunnerConfig], None],
) -> None:
    config = runner.load_offline_batch_runner_config(_write_config(tmp_path))

    def capability_missing(_name: str, _version: str) -> bool:
        return False

    monkeypatch.setattr(rust_backend, "has_native_capability", capability_missing)

    with pytest.raises(
        runner.OfflineBatchConfigurationError,
        match="Required native capabilities are unavailable",
    ):
        stub_runtime_readiness(config)


def test_runner_rejects_second_local_instance_without_starting_process(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = runner.load_offline_batch_runner_config(_write_config(tmp_path))
    started = False

    def forbidden_start(
        _command: tuple[str, ...],
        _cwd: Path,
    ) -> _FakeProcess:
        nonlocal started
        started = True
        return _FakeProcess()

    with advisory_file_lock(lock_path=config.lock_path):
        with caplog.at_level("ERROR", logger=runner.__name__):
            with pytest.raises(runner.OfflineBatchAlreadyRunning):
                runner.run_offline_batch(config, process_factory=forbidden_start)

    assert started is False
    payloads = _event_payloads(caplog)
    assert payloads[-1]["event"] == "offline_batch.runner.lock_rejected"
    assert payloads[-1]["metric_name"] == "offline_batch_lock_contention_total"
    summaries = list(config.summary_directory.glob("*.json"))
    assert len(summaries) == 1
    persisted = runner.OfflineBatchRunSummary.model_validate_json(
        summaries[0].read_text(encoding="utf-8")
    )
    assert persisted.status == "lock_rejected"
    assert persisted.exit_code == 75


def test_runner_runtime_limit_terminates_and_reaps_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = runner.load_offline_batch_runner_config(
        _write_config(tmp_path, max_runtime_seconds=0.01)
    )
    process = _FakeProcess(wait_result=None)
    signals: list[int] = []

    def record_signal(target: _FakeProcess, signal_number: int) -> None:
        assert target is process
        signals.append(signal_number)
        target.stop_signal = signal_number

    monkeypatch.setattr(runner, "_signal_process_group", record_signal)

    with pytest.raises(runner.OfflineBatchRuntimeExceeded):
        runner.run_offline_batch(
            config,
            process_factory=lambda _command, _cwd: process,
        )

    assert signals == [signal.SIGTERM]
    assert process.returncode == -signal.SIGTERM


@pytest.mark.skipif(
    not hasattr(signal, "raise_signal"),
    reason="signal.raise_signal is unavailable",
)
def test_runner_operator_signal_terminates_and_reaps_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = runner.load_offline_batch_runner_config(_write_config(tmp_path))
    process = _FakeProcess(wait_result=None)
    process.on_first_wait = lambda: signal.raise_signal(signal.SIGTERM)
    signals: list[int] = []

    def record_signal(target: _FakeProcess, signal_number: int) -> None:
        signals.append(signal_number)
        target.stop_signal = signal_number

    monkeypatch.setattr(runner, "_signal_process_group", record_signal)

    with pytest.raises(runner.OfflineBatchInterrupted) as exc_info:
        runner.run_offline_batch(
            config,
            process_factory=lambda _command, _cwd: process,
        )

    assert exc_info.value.signal_number == signal.SIGTERM
    assert signals == [signal.SIGTERM]
    assert process.returncode == -signal.SIGTERM


def test_runner_nonzero_exit_is_loud_and_observable(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = runner.load_offline_batch_runner_config(_write_config(tmp_path))

    with caplog.at_level("ERROR", logger=runner.__name__):
        with pytest.raises(runner.OfflineBatchExecutionError) as exc_info:
            runner.run_offline_batch(
                config,
                process_factory=lambda _command, _cwd: _FakeProcess(wait_result=7),
            )

    assert exc_info.value.return_code == 7
    payloads = _event_payloads(caplog)
    assert payloads[-1]["event"] == "offline_batch.runner.failed"
    assert payloads[-1]["exit_code"] == 7

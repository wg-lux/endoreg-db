from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from hashlib import sha256
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Literal, Protocol, cast
from uuid import uuid4

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)

from endoreg_db.utils.file_operations import (
    advisory_file_lock,
    atomic_write_file,
)
from endoreg_db.utils.structured_logging import emit_structured_event
from workflow.scripts.import_common import RuleResources, WorkflowConfig


logger = logging.getLogger(__name__)


class NativeCapabilityRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    contract_version: str = Field(min_length=1)


class OfflineBatchResourceBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cores: int = Field(ge=1)
    mem_mb: int = Field(ge=1)
    gpu: int = Field(default=0, ge=0)
    rust_workers: int = Field(ge=1)

    def resource_arguments(self) -> tuple[str, ...]:
        return (
            f"mem_mb={self.mem_mb}",
            f"gpu={self.gpu}",
            f"rust_workers={self.rust_workers}",
        )


class OfflineBatchRunnerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    workflow_root: Path
    snakefile: Path
    workflow_config: Path
    profile: Path
    lock_path: Path
    summary_directory: Path
    resources: OfflineBatchResourceBudget
    required_native_capabilities: tuple[NativeCapabilityRequirement, ...]
    assert_environment_readiness: bool = True
    lock_timeout_seconds: float = Field(default=0.0, ge=0.0)
    shutdown_grace_seconds: float = Field(default=30.0, gt=0.0)
    max_runtime_seconds: float = Field(default=86_400.0, gt=0.0)
    heartbeat_seconds: float = Field(default=30.0, gt=0.0)
    poll_interval_seconds: float = Field(default=1.0, gt=0.0)
    _supervisor_config_sha256: str = PrivateAttr(default="")
    _workflow_config_sha256: str = PrivateAttr(default="")

    @property
    def supervisor_config_sha256(self) -> str:
        return self._supervisor_config_sha256

    @property
    def workflow_config_sha256(self) -> str:
        return self._workflow_config_sha256

    def attach_configuration_sha256(
        self,
        *,
        supervisor_config_sha256: str,
        workflow_config_sha256: str,
    ) -> None:
        self._supervisor_config_sha256 = supervisor_config_sha256
        self._workflow_config_sha256 = workflow_config_sha256

    def command(self, *, batch_id: str) -> tuple[str, ...]:
        return (
            sys.executable,
            "-m",
            "snakemake",
            "--snakefile",
            str(self.snakefile),
            "--profile",
            str(self.profile),
            "--configfile",
            str(self.workflow_config),
            "--cores",
            str(self.resources.cores),
            "--resources",
            *self.resources.resource_arguments(),
            "--config",
            f"batch_id={batch_id}",
        )


class OfflineBatchRunnerError(RuntimeError):
    """Base class for safe, operator-visible offline batch failures."""


class OfflineBatchConfigurationError(OfflineBatchRunnerError):
    pass


class OfflineBatchAlreadyRunning(OfflineBatchRunnerError):
    pass


class OfflineBatchExecutionError(OfflineBatchRunnerError):
    def __init__(self, return_code: int) -> None:
        super().__init__(f"Snakemake exited with status {return_code}.")
        self.return_code = return_code


class OfflineBatchRuntimeExceeded(OfflineBatchRunnerError):
    pass


class OfflineBatchInterrupted(OfflineBatchRunnerError):
    def __init__(self, signal_number: int) -> None:
        signal_name = signal.Signals(signal_number).name
        super().__init__(f"Offline batch interrupted by {signal_name}.")
        self.signal_number = signal_number
        self.signal_name = signal_name


class OfflineBatchRunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    batch_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    supervisor_config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    workflow_config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    started_at: datetime
    completed_at: datetime
    status: Literal[
        "completed",
        "failed",
        "interrupted",
        "lock_rejected",
        "runtime_exceeded",
    ]
    exit_code: int
    duration_seconds: float = Field(ge=0)
    failure_count: Literal[0, 1]

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("terminal timestamps must be timezone-aware")
        if value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("terminal timestamps must use Coordinated Universal Time")
        return value

    @model_validator(mode="after")
    def validate_terminal_state(self) -> "OfflineBatchRunSummary":
        expected_failure_count = 0 if self.status == "completed" else 1
        if self.failure_count != expected_failure_count:
            raise ValueError("failure_count does not match terminal status")
        if self.status == "completed" and self.exit_code != 0:
            raise ValueError("completed summary must have exit_code zero")
        if self.status != "completed" and self.exit_code == 0:
            raise ValueError("failed summary must have a nonzero exit_code")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        return self


OfflineBatchRunResult = OfflineBatchRunSummary


class _Process(Protocol):
    pid: int
    returncode: int | None

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


ProcessFactory = Callable[[tuple[str, ...], Path], _Process]


@dataclass
class _ShutdownState:
    signal_number: int | None = None


def _load_yaml_mapping(path: Path, *, label: str) -> Mapping[str, object]:
    try:
        payload: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OfflineBatchConfigurationError(f"Cannot read {label}.") from exc
    except yaml.YAMLError as exc:
        raise OfflineBatchConfigurationError(f"{label} is not valid YAML.") from exc
    if not isinstance(payload, dict):
        raise OfflineBatchConfigurationError(
            f"{label} must be a YAML mapping with string keys."
        )
    untyped_payload = cast(dict[object, object], payload)
    if not all(isinstance(key, str) for key in untyped_payload):
        raise OfflineBatchConfigurationError(
            f"{label} must be a YAML mapping with string keys."
        )
    return cast(dict[str, object], untyped_payload)


def _resolve_config_path(value: Path, *, base_directory: Path) -> Path:
    candidate = value if value.is_absolute() else base_directory / value
    return candidate.absolute()


def _require_regular_file(path: Path, *, label: str) -> None:
    if path.is_symlink():
        raise OfflineBatchConfigurationError(f"{label} must not be a symbolic link.")
    if not path.is_file():
        raise OfflineBatchConfigurationError(f"{label} is not a regular file.")


def _require_directory(path: Path, *, label: str) -> None:
    if path.is_symlink():
        raise OfflineBatchConfigurationError(f"{label} must not be a symbolic link.")
    if not path.is_dir():
        raise OfflineBatchConfigurationError(f"{label} is not a directory.")


def _require_path_within_directory(
    path: Path,
    *,
    directory: Path,
    label: str,
) -> None:
    resolved_path = path.resolve(strict=False)
    resolved_directory = directory.resolve()
    if not resolved_path.is_relative_to(resolved_directory):
        raise OfflineBatchConfigurationError(f"{label} must stay inside workflow_root.")


def _assert_job_resources_fit_budget(
    *,
    stage: str,
    job_identifiers: tuple[str, ...],
    required: RuleResources,
    available: OfflineBatchResourceBudget,
) -> None:
    if not job_identifiers:
        return
    exceeded: list[str] = []
    if required.threads > available.cores:
        exceeded.append(f"threads={required.threads} > cores={available.cores}")
    if required.mem_mb > available.mem_mb:
        exceeded.append(f"mem_mb={required.mem_mb} > mem_mb={available.mem_mb}")
    if required.gpu > available.gpu:
        exceeded.append(f"gpu={required.gpu} > gpu={available.gpu}")
    if required.rust_workers > available.rust_workers:
        exceeded.append(
            "rust_workers="
            f"{required.rust_workers} > rust_workers={available.rust_workers}"
        )
    if exceeded:
        detail = ", ".join(exceeded)
        raise OfflineBatchConfigurationError(
            f"Runner resource budget cannot schedule configured {stage} jobs: {detail}."
        )


def _validate_workflow_resource_budgets(
    workflow_config: WorkflowConfig,
    runner_resources: OfflineBatchResourceBudget,
) -> None:
    stages = (
        (
            "video_import",
            tuple(workflow_config.video_imports),
            workflow_config.resources.video,
        ),
        (
            "report_import",
            tuple(workflow_config.report_imports),
            workflow_config.resources.report,
        ),
        (
            "video_transcode",
            tuple(workflow_config.video_transcodes),
            workflow_config.resources.resolved_video_transcode,
        ),
        (
            "video_hls_materialization",
            tuple(workflow_config.video_hls_materializations),
            workflow_config.resources.resolved_video_hls,
        ),
    )
    for stage, job_identifiers, required in stages:
        _assert_job_resources_fit_budget(
            stage=stage,
            job_identifiers=job_identifiers,
            required=required,
            available=runner_resources,
        )


def _validate_native_capability_policy(
    workflow_config: WorkflowConfig,
    config: OfflineBatchRunnerConfig,
) -> None:
    required_pairs = {
        (requirement.name, requirement.contract_version)
        for requirement in config.required_native_capabilities
    }
    if (
        workflow_config.video_imports
        and (
            "batch_file_identity",
            "batch_file_identity_v1",
        )
        not in required_pairs
    ):
        raise OfflineBatchConfigurationError(
            "Configured video imports require native capability "
            "batch_file_identity/batch_file_identity_v1."
        )


def _assert_runtime_readiness(config: OfflineBatchRunnerConfig) -> None:
    from endoreg_db.services.environment_readiness import (
        assert_environment_readiness,
    )
    from endoreg_db.utils.rust_backend import has_native_capability

    missing = [
        f"{requirement.name}/{requirement.contract_version}"
        for requirement in config.required_native_capabilities
        if not has_native_capability(
            requirement.name,
            requirement.contract_version,
        )
    ]
    if missing:
        raise OfflineBatchConfigurationError(
            "Required native capabilities are unavailable: " + ", ".join(missing)
        )
    if config.assert_environment_readiness:
        try:
            assert_environment_readiness()
        except RuntimeError as exc:
            raise OfflineBatchConfigurationError(
                "Offline batch environment readiness check failed."
            ) from exc


def _configuration_digest(config_path: Path) -> str:
    try:
        return sha256(config_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise OfflineBatchConfigurationError(
            "Cannot read configuration while computing its audit digest."
        ) from exc


def load_offline_batch_runner_config(path: Path) -> OfflineBatchRunnerConfig:
    config_path = path.absolute()
    _require_regular_file(config_path, label="runner configuration")
    payload = _load_yaml_mapping(
        config_path,
        label="Offline batch runner configuration",
    )
    try:
        raw_config = OfflineBatchRunnerConfig.model_validate(payload)
    except ValidationError as exc:
        raise OfflineBatchConfigurationError(
            f"Invalid offline batch runner configuration: {exc}"
        ) from exc

    base_directory = config_path.parent
    resolved = raw_config.model_copy(
        update={
            "workflow_root": _resolve_config_path(
                raw_config.workflow_root,
                base_directory=base_directory,
            ),
            "snakefile": _resolve_config_path(
                raw_config.snakefile,
                base_directory=base_directory,
            ),
            "workflow_config": _resolve_config_path(
                raw_config.workflow_config,
                base_directory=base_directory,
            ),
            "profile": _resolve_config_path(
                raw_config.profile,
                base_directory=base_directory,
            ),
            "lock_path": _resolve_config_path(
                raw_config.lock_path,
                base_directory=base_directory,
            ),
            "summary_directory": _resolve_config_path(
                raw_config.summary_directory,
                base_directory=base_directory,
            ),
        }
    )
    _require_directory(resolved.workflow_root, label="workflow_root")
    _require_regular_file(resolved.snakefile, label="snakefile")
    _require_regular_file(resolved.workflow_config, label="workflow_config")
    _require_directory(resolved.profile, label="profile")
    _require_path_within_directory(
        resolved.lock_path,
        directory=resolved.workflow_root,
        label="lock_path",
    )
    _require_path_within_directory(
        resolved.summary_directory,
        directory=resolved.workflow_root,
        label="summary_directory",
    )
    if resolved.summary_directory.is_symlink():
        raise OfflineBatchConfigurationError(
            "summary_directory must not be a symbolic link."
        )
    if resolved.lock_path.is_symlink():
        raise OfflineBatchConfigurationError("lock_path must not be a symbolic link.")
    if resolved.poll_interval_seconds > resolved.heartbeat_seconds:
        raise OfflineBatchConfigurationError(
            "poll_interval_seconds cannot exceed heartbeat_seconds."
        )
    if resolved.resources.rust_workers > resolved.resources.cores:
        raise OfflineBatchConfigurationError(
            "rust_workers cannot exceed the runner core budget."
        )
    workflow_payload = _load_yaml_mapping(
        resolved.workflow_config,
        label="Snakemake workflow configuration",
    )
    try:
        workflow_config = WorkflowConfig.model_validate(workflow_payload)
    except ValidationError as exc:
        raise OfflineBatchConfigurationError(
            f"Invalid Snakemake workflow configuration: {exc}"
        ) from exc
    _validate_workflow_resource_budgets(workflow_config, resolved.resources)
    _validate_native_capability_policy(workflow_config, resolved)
    resolved.attach_configuration_sha256(
        supervisor_config_sha256=_configuration_digest(config_path),
        workflow_config_sha256=workflow_config.configuration_sha256(),
    )
    return resolved


def _start_process(command: tuple[str, ...], cwd: Path) -> _Process:
    return subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        umask=0o077,
    )


@contextmanager
def _shutdown_signal_handlers(state: _ShutdownState):
    if not hasattr(signal, "SIGTERM") or not hasattr(signal, "SIGINT"):
        yield
        return

    def request_shutdown(
        signal_number: int,
        _frame: FrameType | None,
    ) -> None:
        if state.signal_number is None:
            state.signal_number = signal_number

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGTERM, request_shutdown)
        signal.signal(signal.SIGINT, request_shutdown)
    except ValueError as exc:
        raise OfflineBatchRunnerError(
            "Offline batch runner must execute in the main thread."
        ) from exc
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)


def _signal_process_group(process: _Process, signal_number: int) -> None:
    os.killpg(process.pid, signal_number)


def _stop_process(
    process: _Process,
    *,
    batch_id: str,
    reason: Literal["operator_shutdown", "runtime_exceeded"],
    grace_seconds: float,
) -> int:
    emit_structured_event(
        logger,
        "offline_batch.runner.termination_requested",
        level=logging.WARNING,
        batch_id=batch_id,
        reason=reason,
        child_pid=process.pid,
        grace_seconds=grace_seconds,
        metric_name="offline_batch_termination_total",
        metric_value=1,
    )
    try:
        _signal_process_group(process, signal.SIGTERM)
    except ProcessLookupError:
        return process.wait()
    try:
        return process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        emit_structured_event(
            logger,
            "offline_batch.runner.termination_escalated",
            level=logging.ERROR,
            batch_id=batch_id,
            reason=reason,
            child_pid=process.pid,
            metric_name="offline_batch_kill_total",
            metric_value=1,
        )
        try:
            _signal_process_group(process, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return process.wait()


def _wait_for_process(
    process: _Process,
    *,
    config: OfflineBatchRunnerConfig,
    batch_id: str,
    shutdown_state: _ShutdownState,
    started_at: float,
) -> int:
    next_heartbeat = started_at + config.heartbeat_seconds
    deadline = started_at + config.max_runtime_seconds
    while True:
        now = time.monotonic()
        if shutdown_state.signal_number is not None:
            signal_number = shutdown_state.signal_number
            emit_structured_event(
                logger,
                "offline_batch.runner.shutdown_requested",
                level=logging.WARNING,
                batch_id=batch_id,
                signal_name=signal.Signals(signal_number).name,
                elapsed_seconds=now - started_at,
                metric_name="offline_batch_shutdown_total",
                metric_value=1,
            )
            _stop_process(
                process,
                batch_id=batch_id,
                reason="operator_shutdown",
                grace_seconds=config.shutdown_grace_seconds,
            )
            raise OfflineBatchInterrupted(signal_number)
        if now >= deadline:
            _stop_process(
                process,
                batch_id=batch_id,
                reason="runtime_exceeded",
                grace_seconds=config.shutdown_grace_seconds,
            )
            raise OfflineBatchRuntimeExceeded(
                "Offline batch exceeded its configured maximum runtime."
            )

        wait_seconds = min(
            config.poll_interval_seconds,
            max(deadline - now, 0.001),
        )
        try:
            return process.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            now = time.monotonic()
            if now >= next_heartbeat:
                emit_structured_event(
                    logger,
                    "offline_batch.runner.heartbeat",
                    batch_id=batch_id,
                    child_pid=process.pid,
                    elapsed_seconds=now - started_at,
                    metric_name="offline_batch_active",
                    metric_value=1,
                )
                next_heartbeat = now + config.heartbeat_seconds


def _terminal_summary(
    *,
    batch_id: str,
    config: OfflineBatchRunnerConfig,
    started_at: datetime,
    started_monotonic: float,
    status: Literal[
        "completed",
        "failed",
        "interrupted",
        "lock_rejected",
        "runtime_exceeded",
    ],
    exit_code: int,
) -> OfflineBatchRunSummary:
    completed_at = datetime.now(UTC)
    return OfflineBatchRunSummary(
        batch_id=batch_id,
        supervisor_config_sha256=config.supervisor_config_sha256,
        workflow_config_sha256=config.workflow_config_sha256,
        started_at=started_at,
        completed_at=completed_at,
        status=status,
        exit_code=exit_code,
        duration_seconds=time.monotonic() - started_monotonic,
        failure_count=0 if status == "completed" else 1,
    )


def _emit_terminal_event(
    *,
    config: OfflineBatchRunnerConfig,
    event: str,
    level: int,
    summary: OfflineBatchRunSummary,
    metric_name: str,
    reason: str | None = None,
    error_type: str | None = None,
    lock_wait_seconds: float | None = None,
) -> OfflineBatchRunSummary:
    payload = f"{summary.model_dump_json(indent=2)}\n".encode("utf-8")
    try:
        atomic_write_file(
            destination=config.summary_directory / f"{summary.batch_id}.json",
            content=(payload,),
            required_bytes=len(payload),
            file_mode=0o600,
            dir_mode=0o700,
        )
    except OSError as exc:
        raise OfflineBatchRunnerError(
            "Failed to persist the offline batch terminal summary."
        ) from exc
    emit_structured_event(
        logger,
        event,
        level=level,
        batch_id=summary.batch_id,
        supervisor_config_sha256=summary.supervisor_config_sha256,
        workflow_config_sha256=summary.workflow_config_sha256,
        started_at=summary.started_at.isoformat(),
        completed_at=summary.completed_at.isoformat(),
        status=summary.status,
        exit_code=summary.exit_code,
        duration_seconds=summary.duration_seconds,
        failure_count=summary.failure_count,
        reason=reason,
        error_type=error_type,
        lock_wait_seconds=lock_wait_seconds,
        metric_name=metric_name,
        metric_value=1,
    )
    return summary


def run_offline_batch(
    config: OfflineBatchRunnerConfig,
    *,
    process_factory: ProcessFactory = _start_process,
) -> OfflineBatchRunResult:
    batch_id = str(uuid4())
    batch_started_at = datetime.now(UTC)
    batch_started_monotonic = time.monotonic()
    lock_started_at = batch_started_monotonic
    try:
        try:
            _assert_runtime_readiness(config)
        except OfflineBatchConfigurationError:
            _emit_terminal_event(
                config=config,
                event="offline_batch.runner.failed",
                level=logging.ERROR,
                summary=_terminal_summary(
                    batch_id=batch_id,
                    config=config,
                    started_at=batch_started_at,
                    started_monotonic=batch_started_monotonic,
                    status="failed",
                    exit_code=78,
                ),
                reason="runtime_readiness_rejected",
                metric_name="offline_batch_failed_total",
            )
            raise
        lock_context = advisory_file_lock(
            lock_path=config.lock_path,
            timeout_seconds=config.lock_timeout_seconds,
        )
        with lock_context:
            lock_wait_seconds = time.monotonic() - lock_started_at
            emit_structured_event(
                logger,
                "offline_batch.runner.lock_acquired",
                batch_id=batch_id,
                lock_wait_seconds=lock_wait_seconds,
                metric_name="offline_batch_lock_wait_seconds",
                metric_value=lock_wait_seconds,
            )
            command = config.command(batch_id=batch_id)
            emit_structured_event(
                logger,
                "offline_batch.runner.started",
                batch_id=batch_id,
                supervisor_config_sha256=config.supervisor_config_sha256,
                workflow_config_sha256=config.workflow_config_sha256,
                started_at=batch_started_at.isoformat(),
                cores=config.resources.cores,
                mem_mb=config.resources.mem_mb,
                gpu=config.resources.gpu,
                rust_workers=config.resources.rust_workers,
                max_runtime_seconds=config.max_runtime_seconds,
                metric_name="offline_batch_started_total",
                metric_value=1,
            )
            started_at = time.monotonic()
            try:
                process = process_factory(command, config.workflow_root)
            except OSError as exc:
                _emit_terminal_event(
                    config=config,
                    event="offline_batch.runner.failed",
                    level=logging.ERROR,
                    summary=_terminal_summary(
                        batch_id=batch_id,
                        config=config,
                        started_at=batch_started_at,
                        started_monotonic=batch_started_monotonic,
                        status="failed",
                        exit_code=127,
                    ),
                    reason="process_start_failed",
                    error_type=exc.__class__.__name__,
                    metric_name="offline_batch_failed_total",
                )
                raise OfflineBatchExecutionError(127) from exc

            shutdown_state = _ShutdownState()
            try:
                with _shutdown_signal_handlers(shutdown_state):
                    return_code = _wait_for_process(
                        process,
                        config=config,
                        batch_id=batch_id,
                        shutdown_state=shutdown_state,
                        started_at=started_at,
                    )
            except (OfflineBatchInterrupted, OfflineBatchRuntimeExceeded) as exc:
                _emit_terminal_event(
                    config=config,
                    event="offline_batch.runner.failed",
                    level=logging.ERROR,
                    summary=_terminal_summary(
                        batch_id=batch_id,
                        config=config,
                        started_at=batch_started_at,
                        started_monotonic=batch_started_monotonic,
                        status=(
                            "interrupted"
                            if isinstance(exc, OfflineBatchInterrupted)
                            else "runtime_exceeded"
                        ),
                        exit_code=(
                            128 + exc.signal_number
                            if isinstance(exc, OfflineBatchInterrupted)
                            else 124
                        ),
                    ),
                    reason=exc.__class__.__name__,
                    metric_name="offline_batch_failed_total",
                )
                raise

            if return_code != 0:
                _emit_terminal_event(
                    config=config,
                    event="offline_batch.runner.failed",
                    level=logging.ERROR,
                    summary=_terminal_summary(
                        batch_id=batch_id,
                        config=config,
                        started_at=batch_started_at,
                        started_monotonic=batch_started_monotonic,
                        status="failed",
                        exit_code=return_code,
                    ),
                    reason="snakemake_nonzero_exit",
                    metric_name="offline_batch_failed_total",
                )
                raise OfflineBatchExecutionError(return_code)
            completed_summary = _emit_terminal_event(
                config=config,
                event="offline_batch.runner.completed",
                level=logging.INFO,
                summary=_terminal_summary(
                    batch_id=batch_id,
                    config=config,
                    started_at=batch_started_at,
                    started_monotonic=batch_started_monotonic,
                    status="completed",
                    exit_code=return_code,
                ),
                metric_name="offline_batch_completed_total",
            )
            return completed_summary
    except TimeoutError as exc:
        _emit_terminal_event(
            config=config,
            event="offline_batch.runner.lock_rejected",
            level=logging.ERROR,
            summary=_terminal_summary(
                batch_id=batch_id,
                config=config,
                started_at=batch_started_at,
                started_monotonic=batch_started_monotonic,
                status="lock_rejected",
                exit_code=75,
            ),
            reason="single_instance_lock_held",
            lock_wait_seconds=time.monotonic() - lock_started_at,
            metric_name="offline_batch_lock_contention_total",
        )
        raise OfflineBatchAlreadyRunning(
            "Another offline batch runner already owns the local instance lock."
        ) from exc


__all__ = [
    "OfflineBatchAlreadyRunning",
    "OfflineBatchConfigurationError",
    "OfflineBatchExecutionError",
    "OfflineBatchInterrupted",
    "NativeCapabilityRequirement",
    "OfflineBatchResourceBudget",
    "OfflineBatchRunResult",
    "OfflineBatchRunSummary",
    "OfflineBatchRunnerConfig",
    "OfflineBatchRunnerError",
    "OfflineBatchRuntimeExceeded",
    "load_offline_batch_runner_config",
    "run_offline_batch",
]

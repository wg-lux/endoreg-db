# pyright: reportPrivateUsage=false
from __future__ import annotations

import inspect
import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast
from unittest.mock import patch

import pytest
from _pytest.logging import LogCaptureFixture

from endoreg_db import celery_observability
from endoreg_db.apps import EndoregDbConfig


@dataclass
class _Request:
    delivery_info: Mapping[str, object]
    retries: object


@dataclass(eq=False)
class _Task:
    name: object
    request: _Request


def _clear_active_tasks() -> None:
    with celery_observability._active_task_lock:
        celery_observability._active_task_started_at.clear()


def test_celery_hooks_emit_bounded_execution_timing_without_payloads(
    caplog: LogCaptureFixture,
) -> None:
    _clear_active_tasks()
    task = _Task(
        name="endoreg_db.tasks.video_hls_materialization",
        request=_Request(delivery_info={"routing_key": "ffmpeg_media"}, retries=2),
    )

    with (
        caplog.at_level(logging.INFO, logger="endoreg_db.workload_timing"),
        patch(
            "endoreg_db.celery_observability.start_workload_timing",
            return_value=10.0,
        ),
        patch(
            "endoreg_db.utils.workload_timing.time.monotonic",
            return_value=13.5,
        ),
    ):
        celery_observability._task_prerun_receiver(
            sender=task,
            task_id="clinical-task-identifier",
            task=task,
            args=("/protected/patient/video.mp4",),
            kwargs={"secret": "do-not-log"},
        )
        celery_observability._task_postrun_receiver(
            sender=task,
            task_id="clinical-task-identifier",
            task=task,
            state="SUCCESS",
            retval={"video_id": 42},
        )

    payload = cast(
        Mapping[str, object], getattr(caplog.records[-1], "structured_event")
    )
    assert payload["event"] == "workload.timing"
    assert payload["operation"] == "celery_task"
    assert payload["outcome"] == "completed"
    assert payload["task_family"] == "video_hls_materialization"
    assert payload["queue"] == "ffmpeg_media"
    assert payload["retry_bucket"] == "2_to_3"
    assert payload["duration_seconds"] == 3.5
    rendered = caplog.text
    assert "clinical-task-identifier" not in rendered
    assert "/protected/patient/video.mp4" not in rendered
    assert "do-not-log" not in rendered
    assert "video_id" not in rendered
    assert not celery_observability._active_task_started_at


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("FAILURE", "failed"),
        ("RETRY", "retry"),
        ("REVOKED", "revoked"),
        ("STARTED", "unknown"),
        (None, "unknown"),
    ],
)
def test_celery_outcomes_are_normalized(state: object, expected: str) -> None:
    assert celery_observability._outcome_from_celery_state(state).value == expected


def test_unknown_task_queue_and_retry_are_normalized(
    caplog: LogCaptureFixture,
) -> None:
    _clear_active_tasks()
    task = _Task(
        name="third.party.patient-specific-task",
        request=_Request(
            delivery_info={"routing_key": "patient-specific-queue"},
            retries="many",
        ),
    )

    with (
        caplog.at_level(logging.INFO, logger="endoreg_db.workload_timing"),
        patch(
            "endoreg_db.celery_observability.start_workload_timing",
            return_value=5.0,
        ),
        patch(
            "endoreg_db.utils.workload_timing.time.monotonic",
            return_value=6.0,
        ),
    ):
        celery_observability._task_prerun_receiver(task_id="internal", task=task)
        celery_observability._task_postrun_receiver(
            task_id="internal", task=task, state="SUCCESS"
        )

    payload = cast(
        Mapping[str, object], getattr(caplog.records[-1], "structured_event")
    )
    assert payload["task_family"] == "unknown"
    assert payload["queue"] == "unknown"
    assert payload["retry_bucket"] == "unknown"
    assert "third.party" not in caplog.text
    assert "patient-specific" not in caplog.text


def test_postrun_without_prerun_does_not_emit_a_fake_duration(
    caplog: LogCaptureFixture,
) -> None:
    _clear_active_tasks()
    task = _Task(
        name="endoreg_db.process_upload_job",
        request=_Request(delivery_info={"routing_key": "pipeline"}, retries=0),
    )

    with caplog.at_level(logging.INFO, logger="endoreg_db.workload_timing"):
        celery_observability._task_postrun_receiver(
            task_id="missing", task=task, state="SUCCESS"
        )

    assert not caplog.records


def test_signal_registration_is_idempotent() -> None:
    with (
        patch.object(celery_observability, "_signals_registered", False),
        patch.object(celery_observability.task_prerun, "connect") as prerun_connect,
        patch.object(celery_observability.task_postrun, "connect") as postrun_connect,
        patch.object(celery_observability.task_failure, "connect") as failure_connect,
    ):
        celery_observability.register_celery_timing_signals()
        celery_observability.register_celery_timing_signals()

    prerun_connect.assert_called_once_with(
        celery_observability._task_prerun_receiver,
        weak=False,
        dispatch_uid=celery_observability._TASK_PRERUN_DISPATCH_UID,
    )
    postrun_connect.assert_called_once_with(
        celery_observability._task_postrun_receiver,
        weak=False,
        dispatch_uid=celery_observability._TASK_POSTRUN_DISPATCH_UID,
    )
    failure_connect.assert_called_once_with(
        celery_observability._task_failure_receiver,
        weak=False,
        dispatch_uid=celery_observability._TASK_FAILURE_DISPATCH_UID,
    )


@pytest.mark.parametrize("started", [False, True])
def test_failure_emits_private_error_without_requiring_timing(
    caplog: LogCaptureFixture,
    started: bool,
) -> None:
    _clear_active_tasks()
    task_id = "patient-specific-delivery"
    task = _Task(
        name="endoreg_db.process_upload_job",
        request=_Request(delivery_info={"routing_key": "pipeline"}, retries=2),
    )
    if started:
        celery_observability._task_prerun_receiver(task_id=task_id, task=task)
    with caplog.at_level(logging.ERROR, logger="endoreg_db.workload_timing"):
        celery_observability._task_failure_receiver(
            sender=task,
            task_id=task_id,
            exception=ValueError("clinical-error-content"),
            args=("clinical-argument",),
            kwargs={"password": "clinical-credential"},
            traceback="clinical-traceback",
            einfo="clinical-exception-info",
        )
    assert len(caplog.records) == 1
    record = caplog.records[0]
    payload = cast(Mapping[str, object], getattr(record, "structured_event"))
    assert record.levelno == logging.ERROR
    assert record.exc_info is None
    assert payload == {
        "event": "celery.task_failure",
        "operation": "celery_task",
        "outcome": "failed",
        "task_id_sha256": hashlib.sha256(task_id.encode()).hexdigest(),
        "task_family": "pipeline_ingest",
        "queue": "pipeline",
        "retry_bucket": "2_to_3",
        "error_classification": "invalid_value",
    }
    assert "clinical-" not in caplog.text
    assert task_id not in caplog.text
    # Failure reporting must not consume the separate postrun timing state.
    assert (task_id in celery_observability._active_task_started_at) is started
    _clear_active_tasks()


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (TimeoutError("private"), "timeout"),
        (ConnectionError("private"), "connection"),
        (PermissionError("private"), "permission"),
        (ValueError("private"), "invalid_value"),
        (TypeError("private"), "unknown"),
        (OSError("private"), "os_error"),
        (RuntimeError("private"), "unknown"),
        (None, "unknown"),
    ],
)
def test_failure_classification_is_bounded(exception: object, expected: str) -> None:
    assert celery_observability._failure_classification(exception) == expected


@pytest.mark.parametrize("task_id", [None, "", 42])
def test_failure_normalizes_unknown_context_without_rendering_exception(
    caplog: LogCaptureFixture,
    task_id: object,
) -> None:
    class ClinicalException(Exception):
        def __str__(self) -> str:
            raise AssertionError("Exception details must never be rendered")

    task = _Task(
        name="clinical-task-name",
        request=_Request(delivery_info={"routing_key": "clinical-queue"}, retries=-1),
    )
    with caplog.at_level(logging.ERROR, logger="endoreg_db.workload_timing"):
        celery_observability._task_failure_receiver(
            sender=task,
            task_id=task_id,
            exception=ClinicalException(),
        )
    payload = cast(
        Mapping[str, object], getattr(caplog.records[-1], "structured_event")
    )
    assert payload["task_id_sha256"] is None
    assert payload["task_family"] == "unknown"
    assert payload["queue"] == "unknown"
    assert payload["retry_bucket"] == "unknown"
    assert payload["error_classification"] == "unknown"
    assert "clinical-" not in caplog.text
    assert "ClinicalException" not in caplog.text


def test_failure_signal_dispatch_reaches_structured_error_handler(
    caplog: LogCaptureFixture,
) -> None:
    celery_observability.register_celery_timing_signals()
    task = _Task(
        name="endoreg_db.process_upload_job",
        request=_Request(delivery_info={"routing_key": "pipeline"}, retries=0),
    )
    with caplog.at_level(logging.ERROR, logger="endoreg_db.workload_timing"):
        celery_observability.task_failure.send(
            sender=task, task_id="signal-delivery", exception=TimeoutError("private")
        )
    failure_records = [
        record
        for record in caplog.records
        if record.name == "endoreg_db.workload_timing"
    ]
    assert len(failure_records) == 1
    payload = cast(
        Mapping[str, object], getattr(failure_records[0], "structured_event")
    )
    assert payload["event"] == "celery.task_failure"
    assert payload["error_classification"] == "timeout"
    assert "private" not in caplog.text


def test_app_ready_registers_celery_timing_before_runtime_early_returns() -> None:
    source = inspect.getsource(EndoregDbConfig.ready)

    assert source.index("register_celery_timing_signals()") < source.index(
        '"pytest" in executable'
    )

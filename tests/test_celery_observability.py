# pyright: reportPrivateUsage=false
from __future__ import annotations

import inspect
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


@dataclass
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


def test_app_ready_registers_celery_timing_before_runtime_early_returns() -> None:
    source = inspect.getsource(EndoregDbConfig.ready)

    assert source.index("register_celery_timing_signals()") < source.index(
        '"pytest" in executable'
    )

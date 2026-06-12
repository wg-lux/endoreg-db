from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Protocol, cast
from unittest.mock import patch

import pytest
from django.conf import settings

from endoreg_db import tasks
from endoreg_db.services.media_operation_gate import MediaOperationDeferred


class _TaskLike(Protocol):
    acks_late: bool
    reject_on_worker_lost: bool | None
    track_started: bool

    def retry(self, *args: Any, **kwargs: Any) -> object: ...


def _current_task(task: object) -> _TaskLike:
    getter = getattr(task, "_get_current_object", None)
    if callable(getter):
        return cast(_TaskLike, getter())
    return cast(_TaskLike, task)


def test_job_tasks_are_configured_for_worker_loss_redelivery() -> None:
    celery_tasks = [
        tasks.run_frame_extraction_request_task,
        tasks.run_video_post_validation_rebuild_task,
        tasks.run_video_temporal_inference_task,
        tasks.run_model_training_task,
        tasks.process_upload_job,
        tasks.refresh_audit_ledger_integrity_status_task,
    ]

    for task in celery_tasks:
        current = _current_task(task)
        assert current.acks_late is True
        assert current.reject_on_worker_lost is True
        assert current.track_started is True


def test_celery_defaults_bound_worker_memory_pressure() -> None:
    assert settings.CELERY_RESULT_BACKEND is None
    assert settings.CELERY_TASK_IGNORE_RESULT is True
    assert settings.CELERY_WORKER_PREFETCH_MULTIPLIER == 1
    assert settings.CELERY_TASK_TRACK_STARTED is True
    assert settings.CELERY_TASK_SOFT_TIME_LIMIT < settings.CELERY_TASK_TIME_LIMIT


def test_task_module_defers_service_imports_until_execution() -> None:
    source = Path(tasks.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    top_level_imports = [
        node for node in tree.body if isinstance(node, ast.Import | ast.ImportFrom)
    ]
    imported_modules = {
        node.module
        for node in top_level_imports
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert not any(module.startswith("services.") for module in imported_modules)


def test_frame_extraction_task_delegates_with_normalized_ids() -> None:
    with patch(
        "endoreg_db.services.jobs.frame_extraction_jobs.run_frame_extraction_request",
        return_value=True,
    ) as runner:
        result = cast(Any, tasks.run_frame_extraction_request_task).run(
            "11", "22", "33"
        )

    assert result is True
    runner.assert_called_once_with(
        request_id=11,
        video_id=22,
        frame_number=33,
    )


def test_video_post_validation_rebuild_task_delegates_with_normalized_args() -> None:
    with patch(
        "endoreg_db.services.jobs.video_post_validation_jobs."
        "_run_video_post_validation_rebuild",
        return_value=True,
    ) as runner:
        result = cast(Any, tasks.run_video_post_validation_rebuild_task).run(
            "42",
            only_validated=1,
            history_id="7",
        )

    assert result is True
    runner.assert_called_once_with(42, only_validated=True, history_id=7)


def test_video_post_validation_rebuild_task_retries_when_media_busy() -> None:
    deferred = MediaOperationDeferred("active stream")
    retry_exc = RuntimeError("retry requested")
    current_task = _current_task(tasks.run_video_post_validation_rebuild_task)

    with (
        patch(
            "endoreg_db.services.jobs.video_post_validation_jobs."
            "_run_video_post_validation_rebuild",
            side_effect=deferred,
        ) as runner,
        patch(
            "endoreg_db.config.env.get_video_post_validation_dispatch_delay_seconds",
            return_value=17,
        ),
        patch.object(current_task, "retry", side_effect=retry_exc) as retry,
        pytest.raises(RuntimeError, match="retry requested"),
    ):
        cast(Any, tasks.run_video_post_validation_rebuild_task).run(
            "42",
            only_validated=1,
            history_id="7",
        )

    runner.assert_called_once_with(42, only_validated=True, history_id=7)
    retry.assert_called_once_with(exc=deferred, countdown=17, max_retries=20)


def test_video_temporal_inference_task_delegates_with_bounded_defaults() -> None:
    with patch(
        "endoreg_db.services.video_temporal_inference._run_video_temporal_inference",
        return_value=True,
    ) as runner:
        result = cast(Any, tasks.run_video_temporal_inference_task).run(
            "42",
            "7",
            frame_source_mode="stream",
        )

    assert result is True
    runner.assert_called_once_with(
        42,
        model_meta_id=7,
        history_id=None,
        replace_prediction_segments=True,
        delete_frames_after=True,
        ocr_frame_fraction=0.001,
        ocr_cap=10,
        temporal_options={},
        test_run=False,
        n_test_frames=10,
        frame_source_mode="stream",
    )


def test_model_training_task_delegates_and_returns_small_result() -> None:
    command_kwargs = {"dataset_id": 42}

    with patch(
        "endoreg_db.services.jobs.model_training_jobs._execute_model_training_run",
        return_value=None,
    ) as runner:
        result = cast(Any, tasks.run_model_training_task).run("run-1", command_kwargs)

    assert result is True
    runner.assert_called_once_with(
        "run-1",
        command_kwargs=command_kwargs,
        raise_on_error=True,
    )


def test_upload_processing_task_delegates_with_normalized_job_id() -> None:
    with patch(
        "endoreg_db.services.hub.process_upload_job",
        return_value=True,
    ) as processor:
        result = cast(Any, tasks.process_upload_job).run(123)

    assert result is True
    processor.assert_called_once_with("123")


def test_refresh_audit_ledger_integrity_task_delegates_to_locked_refresh() -> None:
    payload = {"status": "verified"}

    with patch(
        "endoreg_db.services.audit_integrity."
        "refresh_audit_ledger_integrity_status_once",
        return_value=payload,
    ) as refresh:
        result = cast(Any, tasks.refresh_audit_ledger_integrity_status_task).run()

    assert result == payload
    refresh.assert_called_once_with()

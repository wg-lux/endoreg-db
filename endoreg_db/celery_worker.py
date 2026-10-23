"""Reject media workers that cannot run process-isolated OCR model blocks."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from importlib import import_module
from typing import Protocol, cast

from celery.concurrency.prefork import TaskPool
from celery.exceptions import WorkerShutdown
from celery.signals import worker_init


class _Queues(Protocol):
    consume_from: Mapping[str, object]


class _Amqp(Protocol):
    queues: _Queues


class _Application(Protocol):
    amqp: _Amqp


class _Worker(Protocol):
    app: _Application
    pool_cls: str | type[object]
    concurrency: int
    max_tasks_per_child: int | None


def assert_model_process_isolation() -> None:
    """Reject old model launchers that cannot run safely in prefork workers."""
    module = import_module("lx_anonymizer.model_loading")
    if getattr(module, "MODEL_PROCESS_ISOLATION_VERSION", None) != 2:
        raise RuntimeError(
            "lx-anonymizer must provide model process isolation version 2; "
            "install the supervised model-worker build before starting imports."
        )


def validate_media_worker(*, sender: object, **_kwargs: object) -> None:
    """Run before Celery starts its pool or consumes any deliveries."""
    from django.conf import settings

    from endoreg_db.utils.video.executable_discovery import (
        assert_media_tools_available,
    )

    worker = cast(_Worker, sender)
    media_queues = {settings.CELERY_PIPELINE_QUEUE, settings.CELERY_FFMPEG_MEDIA_QUEUE}
    selected_queues = worker.app.amqp.queues.consume_from
    model_queues = media_queues | {settings.CELERY_LLM_INFERENCE_QUEUE}
    if not model_queues.intersection(selected_queues):
        return

    if (
        worker.pool_cls
        not in ("prefork", "celery.concurrency.prefork:TaskPool", TaskPool)
        or worker.max_tasks_per_child != 1
    ):
        logging.getLogger(__name__).critical(
            "Media import workers require --pool=prefork --max-tasks-per-child=1 "
            "to retain task deadlines and recycle model state after each job."
        )
        # Celery signals catch Exception; WorkerShutdown derives from SystemExit
        # so an invalid worker exits instead of merely logging and consuming jobs.
        raise WorkerShutdown(1)
    try:
        assert_model_process_isolation()
        if media_queues.intersection(selected_queues):
            assert_media_tools_available()
    except (ImportError, RuntimeError) as exc:
        logging.getLogger(__name__).critical("Media worker preflight failed: %s", exc)
        raise WorkerShutdown(1) from exc


worker_init.connect(
    validate_media_worker,
    weak=False,
    dispatch_uid="endoreg_db.media_worker_preflight",
)

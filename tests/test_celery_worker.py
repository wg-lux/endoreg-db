from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from celery.concurrency.prefork import TaskPool
from celery.exceptions import WorkerShutdown
from celery.signals import worker_init
from django.test import override_settings

from endoreg_db import celery_worker
from endoreg_db.celery_worker import validate_media_worker
from endoreg_db.utils.video import executable_discovery

pytestmark = pytest.mark.no_db


@dataclass(eq=False)
class _WorkerSender:
    pool_cls: str | type[object]
    concurrency: int
    app: SimpleNamespace
    max_tasks_per_child: int = 1


def _worker(
    pool: str | type[object], queues: list[str], concurrency: int = 1
) -> _WorkerSender:
    return _WorkerSender(
        pool_cls=pool,
        concurrency=concurrency,
        app=SimpleNamespace(
            amqp=SimpleNamespace(
                queues=SimpleNamespace(consume_from=dict.fromkeys(queues))
            )
        ),
    )


@override_settings(
    CELERY_PIPELINE_QUEUE="site_import", CELERY_FFMPEG_MEDIA_QUEUE="site_media"
)
@pytest.mark.parametrize("queue", ["site_import", "site_media"])
@pytest.mark.parametrize("pool", ["solo", "threads", "eventlet", "gevent"])
def test_incompatible_media_pool_aborts_registered_signal(
    monkeypatch: pytest.MonkeyPatch,
    queue: str,
    pool: str,
) -> None:
    preflight = Mock()
    monkeypatch.setattr(executable_discovery, "assert_media_tools_available", preflight)

    with pytest.raises(WorkerShutdown) as raised:
        worker_init.send(sender=_worker(pool, ["maintenance", queue]))

    assert raised.value.code == 1
    preflight.assert_not_called()


@override_settings(
    CELERY_PIPELINE_QUEUE="site_import", CELERY_FFMPEG_MEDIA_QUEUE="site_media"
)
@pytest.mark.parametrize(
    "pool", ["prefork", "celery.concurrency.prefork:TaskPool", TaskPool]
)
def test_prefork_media_worker_checks_tools_before_consumption(
    monkeypatch: pytest.MonkeyPatch,
    pool: str | type[object],
) -> None:
    preflight = Mock()
    monkeypatch.setattr(executable_discovery, "assert_media_tools_available", preflight)
    worker_init.send(sender=_worker(pool, ["site_media"]))
    preflight.assert_called_once_with()


@override_settings(
    CELERY_PIPELINE_QUEUE="site_import", CELERY_FFMPEG_MEDIA_QUEUE="site_media"
)
def test_media_worker_requires_per_job_recycling() -> None:
    worker = _worker("prefork", ["site_import"], concurrency=2)
    worker.max_tasks_per_child = 2
    with pytest.raises(WorkerShutdown):
        validate_media_worker(sender=worker)


@override_settings(
    CELERY_PIPELINE_QUEUE="site_import", CELERY_FFMPEG_MEDIA_QUEUE="site_media"
)
def test_media_tool_failure_aborts_registered_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        executable_discovery,
        "assert_media_tools_available",
        Mock(side_effect=RuntimeError("ffprobe is unavailable")),
    )
    with pytest.raises(WorkerShutdown) as raised:
        worker_init.send(sender=_worker("prefork", ["site_import"]))
    assert raised.value.code == 1


@override_settings(
    CELERY_PIPELINE_QUEUE="site_import", CELERY_FFMPEG_MEDIA_QUEUE="site_media"
)
def test_unrelated_worker_pool_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    preflight = Mock()
    monkeypatch.setattr(executable_discovery, "assert_media_tools_available", preflight)
    worker_init.send(
        sender=_worker("prefork", ["maintenance", "inference"], concurrency=4)
    )
    preflight.assert_not_called()


@override_settings(CELERY_LLM_INFERENCE_QUEUE="site_reports")
@pytest.mark.parametrize("pool", ["prefork", "solo"])
def test_report_model_worker_requires_prefork_without_video_tools(
    monkeypatch: pytest.MonkeyPatch,
    pool: str,
) -> None:
    preflight = Mock()
    monkeypatch.setattr(executable_discovery, "assert_media_tools_available", preflight)
    if pool == "solo":
        with pytest.raises(WorkerShutdown):
            worker_init.send(sender=_worker(pool, ["site_reports"]))
    else:
        worker_init.send(sender=_worker(pool, ["site_reports"]))
    preflight.assert_not_called()


@pytest.fixture(autouse=True)
def compatible_model_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        celery_worker,
        "import_module",
        Mock(return_value=SimpleNamespace(MODEL_PROCESS_ISOLATION_VERSION=2)),
    )


@pytest.mark.parametrize("version", [None, 1, 3])
@override_settings(CELERY_PIPELINE_QUEUE="site_import")
def test_old_or_unknown_model_launcher_aborts_startup(
    monkeypatch: pytest.MonkeyPatch,
    version: int | None,
) -> None:
    monkeypatch.setattr(
        celery_worker,
        "import_module",
        Mock(return_value=SimpleNamespace(MODEL_PROCESS_ISOLATION_VERSION=version)),
    )
    preflight = Mock()
    monkeypatch.setattr(executable_discovery, "assert_media_tools_available", preflight)
    with pytest.raises(WorkerShutdown):
        worker_init.send(sender=_worker("prefork", ["site_import"]))
    preflight.assert_not_called()

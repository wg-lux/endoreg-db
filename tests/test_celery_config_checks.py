from __future__ import annotations

from collections.abc import Generator

import pytest
from pytest import MonkeyPatch
from django.test import override_settings

from endoreg_db.checks import check_celery_runtime_configuration
from endoreg_db.services.jobs.heavy_jobs import (
    HeavyJobKind,
    ensure_secure_transport_for_job_kind,
)


CELERY_MODE_ENV_KEYS = (
    "VIDEO_POST_VALIDATION_JOB_MODE",
    "VIDEO_TEMPORAL_INFERENCE_JOB_MODE",
    "VIDEO_REIMPORT_JOB_MODE",
    "REPORT_LLM_JOB_MODE",
    "MODEL_TRAINING_JOB_MODE",
)


@pytest.fixture(autouse=True)
def clear_celery_mode_env(monkeypatch: MonkeyPatch) -> Generator[None, None, None]:
    for key in CELERY_MODE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("CELERY_BROKER_SECURE_TRANSPORT_CONFIRMED", raising=False)
    monkeypatch.delenv("CELERY_REQUIRE_SECURE_TRANSPORT", raising=False)
    yield


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=False,
    CELERY_BROKER_URL="",
    CELERY_RUNTIME_CONFIG_STRICT=True,
    CELERY_REQUIRE_SECURE_TRANSPORT=False,
    WATCHER_CELERY_INLINE_FALLBACK_ENABLED=False,
    MODEL_TRAINING_JOB_MODE="celery",
)
def test_celery_check_errors_without_broker_in_strict_profile() -> None:
    messages = check_celery_runtime_configuration()

    assert [message.id for message in messages] == ["endoreg_db.E001"]
    assert "CELERY_BROKER_URL" in messages[0].msg


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=False,
    CELERY_BROKER_URL="",
    CELERY_RUNTIME_CONFIG_STRICT=False,
    CELERY_REQUIRE_SECURE_TRANSPORT=False,
    WATCHER_CELERY_INLINE_FALLBACK_ENABLED=True,
    MODEL_TRAINING_JOB_MODE="celery",
)
def test_celery_check_warns_without_broker_in_non_strict_profile() -> None:
    messages = check_celery_runtime_configuration()

    assert [message.id for message in messages] == ["endoreg_db.W001"]


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=False,
    CELERY_BROKER_URL="redis://broker.local/0",
    CELERY_RUNTIME_CONFIG_STRICT=True,
    CELERY_REQUIRE_SECURE_TRANSPORT=False,
    WATCHER_CELERY_INLINE_FALLBACK_ENABLED=False,
    MODEL_TRAINING_JOB_MODE="celery",
)
def test_celery_check_errors_on_insecure_broker_in_strict_profile() -> None:
    messages = check_celery_runtime_configuration()

    assert [message.id for message in messages] == ["endoreg_db.E002"]
    assert "rediss:// or amqps://" in messages[0].msg


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=False,
    CELERY_BROKER_URL="redis://broker.local/0",
    CELERY_RUNTIME_CONFIG_STRICT=True,
    CELERY_REQUIRE_SECURE_TRANSPORT=False,
    WATCHER_CELERY_INLINE_FALLBACK_ENABLED=False,
    MODEL_TRAINING_JOB_MODE="celery",
)
def test_celery_check_allows_confirmed_external_secure_transport(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("CELERY_BROKER_SECURE_TRANSPORT_CONFIRMED", "1")

    assert check_celery_runtime_configuration() == []


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=False,
    CELERY_BROKER_URL="rediss://broker.local/0",
    CELERY_RUNTIME_CONFIG_STRICT=True,
    CELERY_REQUIRE_SECURE_TRANSPORT=False,
    WATCHER_CELERY_INLINE_FALLBACK_ENABLED=True,
    MODEL_TRAINING_JOB_MODE="celery",
)
def test_celery_check_rejects_watcher_inline_fallback_in_strict_profile() -> None:
    messages = check_celery_runtime_configuration()

    assert [message.id for message in messages] == ["endoreg_db.E003"]
    assert "WATCHER_CELERY_INLINE_FALLBACK_ENABLED" in messages[0].msg


def test_generic_secure_transport_gate_covers_non_ffmpeg_queues(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("CELERY_REQUIRE_SECURE_TRANSPORT", "1")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://broker.local/0")

    with pytest.raises(RuntimeError, match="secure broker transport"):
        ensure_secure_transport_for_job_kind(HeavyJobKind.REPORT_LLM_IMPORT)

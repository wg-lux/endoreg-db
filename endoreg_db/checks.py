from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from types import NoneType

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, Warning, register

from endoreg_db.config.env import (
    celery_broker_secure_transport_confirmed,
    celery_broker_transport_error,
    celery_broker_url_uses_secure_transport,
)


@dataclass(frozen=True)
class CeleryMode:
    env_key: str
    default: str
    label: str


@dataclass(frozen=True)
class CeleryRuntimeConfiguration:
    broker_url: str
    strict: bool
    watcher_inline_fallback: bool
    require_secure_transport: bool
    enabled_job_labels: str


CELERY_MODES = (
    CeleryMode(
        "VIDEO_POST_VALIDATION_JOB_MODE",
        "celery",
        "video post-validation",
    ),
    CeleryMode(
        "VIDEO_TEMPORAL_INFERENCE_JOB_MODE",
        "celery",
        "video temporal inference",
    ),
    CeleryMode("VIDEO_REIMPORT_JOB_MODE", "celery", "video re-import"),
    CeleryMode("REPORT_LLM_JOB_MODE", "celery", "report LLM"),
    CeleryMode("MODEL_TRAINING_JOB_MODE", "celery", "model training"),
)
ALWAYS_CELERY_LABELS = ("upload pipeline ingest",)
type AppConfigSequence = Sequence[AppConfig] | NoneType
type DatabaseAliasSequence = Sequence[str] | NoneType
type CheckKwargValue = str | bool | int | AppConfigSequence | DatabaseAliasSequence


def _job_mode(mode: CeleryMode) -> str:
    configured = os.environ.get(mode.env_key)
    if configured is None:
        configured = getattr(settings, mode.env_key, mode.default)
    normalized = str(configured or mode.default).strip().lower()
    if normalized not in {"celery", "thread", "inline"}:
        return mode.default
    return normalized


def _celery_enabled_labels() -> list[str]:
    return [
        *ALWAYS_CELERY_LABELS,
        *[mode.label for mode in CELERY_MODES if _job_mode(mode) == "celery"],
    ]


def _celery_runtime_configuration() -> CeleryRuntimeConfiguration:
    strict = bool(getattr(settings, "CELERY_RUNTIME_CONFIG_STRICT", False))
    return CeleryRuntimeConfiguration(
        broker_url=str(getattr(settings, "CELERY_BROKER_URL", "") or "").strip(),
        strict=strict,
        watcher_inline_fallback=bool(
            getattr(settings, "WATCHER_CELERY_INLINE_FALLBACK_ENABLED", False)
        ),
        require_secure_transport=strict
        or bool(getattr(settings, "CELERY_REQUIRE_SECURE_TRANSPORT", False)),
        enabled_job_labels=", ".join(_celery_enabled_labels()),
    )


def _watcher_inline_fallback_error(
    configuration: CeleryRuntimeConfiguration,
) -> Error | None:
    if not configuration.strict or not configuration.watcher_inline_fallback:
        return None
    return Error(
        (
            "WATCHER_CELERY_INLINE_FALLBACK_ENABLED is not allowed when "
            "CELERY_RUNTIME_CONFIG_STRICT is enabled."
        ),
        hint=(
            "Disable the watcher inline fallback for production/clinical "
            "profiles, or run a non-strict development settings module."
        ),
        id="endoreg_db.E003",
    )


def _broker_transport_message(
    configuration: CeleryRuntimeConfiguration,
) -> CheckMessage | None:
    broker_error = celery_broker_transport_error(
        broker_url=configuration.broker_url,
        require_broker=True,
        require_secure_transport=False,
        workload="Celery",
    )
    if broker_error is None:
        return None

    message = (
        f"{broker_error} Celery-backed jobs enabled: "
        f"{configuration.enabled_job_labels}."
    )
    if configuration.strict:
        return Error(
            message,
            hint=(
                "Set CELERY_BROKER_URL or switch affected *_JOB_MODE "
                "values to inline/thread for this profile."
            ),
            id="endoreg_db.E001",
        )
    return Warning(
        message,
        hint="Set CELERY_BROKER_URL before relying on asynchronous processing.",
        id="endoreg_db.W001",
    )


def _secure_broker_transport_error(
    configuration: CeleryRuntimeConfiguration,
) -> Error | None:
    if not configuration.require_secure_transport:
        return None
    if celery_broker_secure_transport_confirmed():
        return None
    if celery_broker_url_uses_secure_transport(configuration.broker_url):
        return None

    return Error(
        (
            "Celery-backed jobs require secure broker transport in this profile. "
            "Configured broker is not rediss:// or amqps://. Jobs enabled: "
            f"{configuration.enabled_job_labels}."
        ),
        hint=(
            "Use rediss:// or amqps://, or set "
            "CELERY_BROKER_SECURE_TRANSPORT_CONFIRMED=1 only when an "
            "external mTLS/VPN boundary already protects the broker."
        ),
        id="endoreg_db.E002",
    )


@register("celery", Tags.security)
def check_celery_runtime_configuration(
    *,
    app_configs: AppConfigSequence = None,
    databases: DatabaseAliasSequence = None,
    **kwargs: CheckKwargValue,
) -> list[CheckMessage]:
    if bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)):
        return []

    configuration = _celery_runtime_configuration()
    messages: list[CheckMessage] = [
        message
        for message in (_watcher_inline_fallback_error(configuration),)
        if message is not None
    ]
    broker_message = _broker_transport_message(configuration)
    if broker_message is not None:
        return [*messages, broker_message]

    secure_transport_error = _secure_broker_transport_error(configuration)
    if secure_transport_error is not None:
        messages.append(secure_transport_error)
    return messages

from __future__ import annotations

import os
from dataclasses import dataclass

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

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


@register("celery", Tags.security)
def check_celery_runtime_configuration(app_configs=None, **kwargs):
    if bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)):
        return []

    broker_url = str(getattr(settings, "CELERY_BROKER_URL", "") or "").strip()
    strict = bool(getattr(settings, "CELERY_RUNTIME_CONFIG_STRICT", False))
    watcher_inline_fallback = bool(
        getattr(settings, "WATCHER_CELERY_INLINE_FALLBACK_ENABLED", False)
    )
    require_secure_transport = strict or bool(
        getattr(settings, "CELERY_REQUIRE_SECURE_TRANSPORT", False)
    )
    celery_enabled = _celery_enabled_labels()
    labels = ", ".join(celery_enabled)
    messages = []

    if strict and watcher_inline_fallback:
        messages.append(
            Error(
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
        )

    broker_error = celery_broker_transport_error(
        broker_url=broker_url,
        require_broker=True,
        require_secure_transport=False,
        workload="Celery",
    )
    if broker_error is not None:
        message = f"{broker_error} Celery-backed jobs enabled: {labels}."
        if strict:
            messages.append(
                Error(
                    message,
                    hint=(
                        "Set CELERY_BROKER_URL or switch affected *_JOB_MODE "
                        "values to inline/thread for this profile."
                    ),
                    id="endoreg_db.E001",
                )
            )
        else:
            messages.append(
                Warning(
                    message,
                    hint=(
                        "Set CELERY_BROKER_URL before relying on asynchronous "
                        "processing."
                    ),
                    id="endoreg_db.W001",
                )
            )
        return messages

    if not require_secure_transport:
        return messages

    if celery_broker_secure_transport_confirmed():
        return messages
    if celery_broker_url_uses_secure_transport(broker_url):
        return messages

    message = (
        "Celery-backed jobs require secure broker transport in this profile. "
        f"Configured broker is not rediss:// or amqps://. Jobs enabled: {labels}."
    )
    messages.append(
        Error(
            message,
            hint=(
                "Use rediss:// or amqps://, or set "
                "CELERY_BROKER_SECURE_TRANSPORT_CONFIRMED=1 only when an "
                "external mTLS/VPN boundary already protects the broker."
            ),
            id="endoreg_db.E002",
        )
    )
    return messages

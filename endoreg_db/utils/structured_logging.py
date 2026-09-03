from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeAlias, cast


SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "csrf",
    "master_key",
    "password",
    "private_key",
    "raw_file_content",
    "raw_media",
    "secret",
    "session",
    "token",
)
MEDIA_FILENAME_RE = re.compile(
    r"\S+\.(?:avi|bin|jpeg|jpg|m4v|mkv|mov|mp4|pdf|png|txt|webm)\b",
    re.IGNORECASE,
)
MAX_LOG_STRING_LENGTH = 512
StructuredLogScalar: TypeAlias = str | int | float | bool | None
StructuredLogValue: TypeAlias = (
    StructuredLogScalar | list["StructuredLogValue"] | dict[str, "StructuredLogValue"]
)
StructuredLogPayload: TypeAlias = dict[str, StructuredLogValue]


def hash_identifier(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def path_reference(path: str | Path) -> StructuredLogPayload:
    raw_path = str(path)
    suffix = Path(raw_path).suffix.lower()
    return {
        "path_sha256": hash_identifier(raw_path),
        "suffix": suffix or None,
    }


def _is_sensitive_key(key: str | None) -> bool:
    if key is None:
        return False
    normalized = key.lower()
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _redacted_string(value: str, reason: str) -> str:
    return f"<redacted:{reason}:sha256={hash_identifier(value)}>"


def sanitize_log_string(value: str, *, key: str | None = None) -> str:
    if _is_sensitive_key(key):
        return "<redacted:sensitive>"
    if "/" in value or "\\" in value:
        return _redacted_string(value, "path_like")
    if MEDIA_FILENAME_RE.search(value):
        return _redacted_string(value, "media_filename")
    if len(value) > MAX_LOG_STRING_LENGTH:
        return f"{value[:MAX_LOG_STRING_LENGTH]}...<truncated:sha256={hash_identifier(value)}>"
    return value


def safe_log_value(value: Any, *, key: str | None = None) -> StructuredLogValue:
    if _is_sensitive_key(key):
        return "<redacted:sensitive>"
    if isinstance(value, Path):
        return path_reference(value)
    if isinstance(value, bytes):
        return {
            "bytes_redacted": True,
            "length": len(value),
        }
    if isinstance(value, str):
        return sanitize_log_string(value, key=key)
    if isinstance(value, Mapping):
        typed_mapping = cast(Mapping[str, Any], value)
        return {
            str(item_key): safe_log_value(item_value, key=str(item_key))
            for item_key, item_value in typed_mapping.items()
        }
    if isinstance(value, list):
        typed_items = cast(list[Any], value)
        return [safe_log_value(item, key=key) for item in typed_items[:100]]
    if isinstance(value, tuple):
        typed_items = cast(tuple[Any, ...], value)
        return [safe_log_value(item, key=key) for item in list(typed_items)[:100]]
    if isinstance(value, set):
        typed_items = cast(set[Any], value)
        return [safe_log_value(item, key=key) for item in list(typed_items)[:100]]
    if isinstance(value, frozenset):
        typed_items = cast(frozenset[Any], value)
        return [safe_log_value(item, key=key) for item in list(typed_items)[:100]]
    if isinstance(value, BaseException):
        return {
            "type": value.__class__.__name__,
            "message": sanitize_log_string(str(value), key=key),
        }
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def safe_log_payload(payload: Mapping[str, Any]) -> StructuredLogPayload:
    return {
        str(key): safe_log_value(value, key=str(key)) for key, value in payload.items()
    }


def emit_structured_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    message: str | None = None,
    **payload: StructuredLogValue,
) -> None:
    structured_payload = safe_log_payload({"event": event, **payload})
    structured_message = (
        sanitize_log_string(message, key="message")
        if message
        else (_default_event_message(event, structured_payload))
    )
    log_message = (
        structured_message
        if message
        else json.dumps(
            structured_payload,
            default=str,
            sort_keys=True,
        )
    )
    extra = {
        "structured_event": structured_payload,
        "structured_message": structured_message,
    }
    if level == logging.DEBUG:
        logger.debug(log_message, extra=extra)
    elif level == logging.INFO:
        logger.info(log_message, extra=extra)
    elif level == logging.WARNING:
        logger.warning(log_message, extra=extra)
    elif level == logging.ERROR:
        logger.error(log_message, extra=extra)
    elif level == logging.CRITICAL:
        logger.critical(log_message, extra=extra)
    else:
        logger.log(level, log_message, extra=extra)


def _default_event_message(
    event: str, payload: Mapping[str, StructuredLogValue]
) -> str:
    reason = payload.get("reason")
    if isinstance(reason, str) and reason:
        return f"{event} reason={reason}"
    status = payload.get("status")
    if isinstance(status, str) and status:
        return f"{event} status={status}"
    return event


class StructuredJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: StructuredLogPayload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": sanitize_log_string(record.getMessage(), key="message"),
        }

        structured_event = getattr(record, "structured_event", None)
        if isinstance(structured_event, Mapping):
            structured_payload = safe_log_payload(
                cast(Mapping[str, Any], structured_event)
            )
            payload.update(structured_payload)
            structured_message = getattr(
                record,
                "structured_message",
                _default_event_message(
                    str(structured_payload.get("event") or "structured_event"),
                    structured_payload,
                ),
            )
            payload["message"] = sanitize_log_string(
                str(structured_message),
                key="message",
            )
        else:
            payload.update(self._json_message_payload(record))

        if record.exc_info:
            exc_type = record.exc_info[0]
            exc_value = record.exc_info[1]
            payload["exception"] = {
                "type": exc_type.__name__ if exc_type is not None else None,
                "message": sanitize_log_string(str(exc_value), key="exception"),
            }

        return json.dumps(payload, default=str, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _json_message_payload(record: logging.LogRecord) -> StructuredLogPayload:
        message = record.getMessage()
        if not message.startswith("{"):
            return {}
        try:
            parsed = json.loads(message)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, Mapping):
            return {}
        return safe_log_payload(cast(Mapping[str, Any], parsed))


def build_production_logging_config(
    *,
    root_level: str = "INFO",
    django_level: str = "INFO",
    app_level: str = "INFO",
) -> dict[str, object]:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structured_json": {
                "()": "endoreg_db.utils.structured_logging.StructuredJsonFormatter",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "structured_json",
                "level": root_level,
            },
        },
        "root": {
            "handlers": ["console"],
            "level": root_level,
        },
        "loggers": {
            "django": {
                "handlers": ["console"],
                "level": django_level,
                "propagate": False,
            },
            "django.server": {
                "handlers": ["console"],
                "level": django_level,
                "propagate": False,
            },
            "endoreg_db": {
                "handlers": ["console"],
                "level": app_level,
                "propagate": False,
            },
        },
    }

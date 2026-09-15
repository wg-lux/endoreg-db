from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from endoreg_db.utils.structured_logging import (
    StructuredLogValue,
    hash_identifier,
    path_reference,
    safe_log_value,
)

_RESET = "\033[0m"
_BOLD = "\033[1m"
_GREEN = "\033[92m"
_CYAN = "\033[96m"
_YELLOW = "\033[93m"
_MAGENTA = "\033[95m"
_BLUE = "\033[94m"
_RED = "\033[91m"
_WHITE = "\033[97m"
_GRAY = "\033[90m"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_TRANSFER_SENSITIVE_EXACT_KEYS = frozenset({"meta", "raw_text", "text"})
_TRANSFER_SENSITIVE_KEY_PARTS = (
    "access_token",
    "anonymized_text",
    "authorization",
    "cookie",
    "credential",
    "csrf",
    "dob",
    "editor_payload",
    "first_name",
    "last_name",
    "master_key",
    "password",
    "patient_context",
    "private_key",
    "processing_snapshot",
    "provenance",
    "raw_meta",
    "raw_media",
    "refresh_token",
    "rendered_text",
    "request_body",
    "resource_rows",
    "secret",
    "session",
    "shared_secret",
    "token",
)
_HASHED_IDENTIFIER_KEYS = frozenset(
    {
        "examination_hash",
        "local_database_id",
        "local_id",
        "node_key",
        "patient_hash",
        "portable_content_hash",
        "resource_hash",
        "source_node",
        "source_node_key",
        "target_node",
        "target_node_key",
        "transfer_key",
    }
)


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")


def _supports_color() -> bool:
    return bool(sys.stdout.isatty() and not os.environ.get("NO_COLOR"))


def enabled() -> bool:
    """Return whether opt-in human-readable transfer diagnostics are enabled."""
    value = os.environ.get("ENDOREG_TRANSFER_VERBOSE", "0")
    return value.strip().lower() in _TRUE_VALUES


def _style(text: str, *codes: str) -> str:
    if not _supports_color():
        return text
    return f"{''.join(codes)}{text}{_RESET}"


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    return normalized in _TRANSFER_SENSITIVE_EXACT_KEYS or any(
        part in normalized for part in _TRANSFER_SENSITIVE_KEY_PARTS
    )


def sanitize(value: object, *, key: str | None = None) -> StructuredLogValue:
    """Recursively narrow transfer diagnostics to privacy-safe JSON values."""
    if key is not None and _is_sensitive_key(key):
        return "<redacted:transfer_sensitive>"
    if isinstance(value, Path):
        return path_reference(value)
    if key is not None and _normalized_key(key) in _HASHED_IDENTIFIER_KEYS:
        return f"<sha256:{hash_identifier(value)}>"
    if isinstance(value, Mapping):
        typed_mapping = cast(Mapping[object, object], value)
        return {
            str(item_key): sanitize(item_value, key=str(item_key))
            for item_key, item_value in typed_mapping.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        typed_sequence = cast(Sequence[object], value)
        return [sanitize(item) for item in list(typed_sequence)[:100]]
    if (
        isinstance(value, (str, bytes, int, float, bool, BaseException))
        or value is None
    ):
        return safe_log_value(value, key=key)
    return f"<{value.__class__.__name__}>"


def section(title: str, icon: str = "") -> None:
    if not enabled():
        return
    label = f"{icon} {title}".strip()
    line = "═" * 96
    print()
    print(_style(line, _BOLD, _GREEN))
    print(_style(label.center(96), _BOLD, _WHITE))
    print(_style(line, _BOLD, _GREEN))


def subsection(title: str) -> None:
    if enabled():
        print()
        print(_style(f"[{title}]", _BOLD, _CYAN))


def decision(title: str) -> None:
    if not enabled():
        return
    inner_width = 92
    print()
    print(_style(f"╔{'═' * inner_width}╗", _BOLD, _MAGENTA))
    print(_style(f"║ {title:<90} ║", _BOLD, _MAGENTA))
    print(_style(f"╚{'═' * inner_width}╝", _BOLD, _MAGENTA))


def step(number: int | str, title: str) -> None:
    if enabled():
        print()
        print(_style(f"▶ STEP {number}: {title}", _BOLD, _YELLOW))


def kv(label: str, value: object, width: int = 32) -> None:
    if not enabled():
        return
    safe_value = sanitize(value, key=label)
    label_text = f"{label:<{width}}"
    if _supports_color():
        print(f"{_style(label_text, _MAGENTA)}: {safe_value}")
    else:
        print(f"{label_text}: {safe_value}")


def info(text: str) -> None:
    if enabled():
        print(_style(f"ℹ {text}", _BLUE))


def success(text: str) -> None:
    if enabled():
        print(_style(f"✓ {text}", _GREEN))


def warning(text: str) -> None:
    if enabled():
        print(_style(f"⚠ {text}", _YELLOW))


def error(text: str) -> None:
    if enabled():
        print(_style(f"✗ {text}", _RED))


def soft_line(char: str = "─", width: int = 96) -> None:
    if enabled():
        print(_style(char * width, _GRAY))


def json_block(title: str, value: Mapping[str, object]) -> None:
    """Print a recursively sanitized mapping; raw scalar dumps are unsupported."""
    if not enabled():
        return
    subsection(title)
    rendered = json.dumps(
        sanitize(value),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )
    print(_style(rendered, _GRAY))


def path_info(
    *,
    label: str,
    path: Path | str | None,
    check_exists: bool = True,
) -> None:
    if not enabled():
        return
    if path is None:
        kv(label, None)
        return
    checked_path = Path(path)
    kv(label, checked_path)
    if check_exists:
        kv(f"{label} exists", checked_path.exists())
        if checked_path.is_file():
            kv(f"{label} size", checked_path.stat().st_size)


def model_identity(
    *,
    model_name: str,
    local_id: object,
    portable_hash: str | None = None,
    node_key: str | None = None,
) -> None:
    subsection(f"{model_name} identity")
    kv("Local database ID", local_id)
    kv("Portable content hash", portable_hash)
    kv("Node key", node_key)


def transfer_summary(
    *,
    transfer_key: str,
    resource_kind: str,
    source_node_key: str,
    target_node_key: str,
    resource_hash: str,
    transfer_mode: str,
) -> None:
    section("ENDOREG HUB TRANSFER", "⇄")
    kv("Transfer key", transfer_key)
    kv("Resource kind", resource_kind)
    kv("Source node key", source_node_key)
    kv("Target node key", target_node_key)
    kv("Resource hash", resource_hash)
    kv("Transfer mode", transfer_mode)

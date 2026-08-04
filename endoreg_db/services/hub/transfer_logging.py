# endoreg_db/services/hub/transfer_logging.py
from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


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


SENSITIVE_KEYS = {
    "secret",
    "node_secret",
    "shared_secret",
    "shared_secret_hash",
    "password",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "patient_first_name",
    "patient_last_name",
    "patient_dob",
    "first_name",
    "last_name",
    "dob",
}


def _supports_color() -> bool:
    if not sys.stdout.isatty():
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return True


def _enabled() -> bool:
    value = os.environ.get("ENDOREG_TRANSFER_VERBOSE", "1")
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _style(text: str, *codes: str) -> str:
    if not _supports_color():
        return text
    return f"{''.join(codes)}{text}{_RESET}"


def _redacted_key(key: object) -> bool:
    normalized = str(key).strip().lower()
    return (
        normalized in SENSITIVE_KEYS
        or "secret" in normalized
        or "password" in normalized
        or "token" in normalized
        or "authorization" in normalized
    )


def sanitize(value: Any, *, key: str | None = None) -> Any:
    """
    Recursively sanitize values before printing.

    Secrets and patient-identifying fields are never printed.
    """
    if key is not None and _redacted_key(key):
        return "<redacted>"

    if isinstance(value, Mapping):
        return {
            str(item_key): sanitize(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }

    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [sanitize(item) for item in value]

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"

    return value


def section(title: str, icon: str = "") -> None:
    if not _enabled():
        return

    label = f"{icon} {title}".strip()
    line = "═" * 96

    print()
    print(_style(line, _BOLD, _GREEN))
    print(_style(label.center(96), _BOLD, _WHITE))
    print(_style(line, _BOLD, _GREEN))


def subsection(title: str) -> None:
    if not _enabled():
        return

    print()
    print(_style(f"[{title}]", _BOLD, _CYAN))


def decision(title: str) -> None:
    if not _enabled():
        return

    inner_width = 92
    print()
    print(_style(f"╔{'═' * inner_width}╗", _BOLD, _MAGENTA))
    print(_style(f"║ {title:<90} ║", _BOLD, _MAGENTA))
    print(_style(f"╚{'═' * inner_width}╝", _BOLD, _MAGENTA))


def step(number: int | str, title: str) -> None:
    if not _enabled():
        return

    print()
    print(_style(f"▶ STEP {number}: {title}", _BOLD, _YELLOW))


def kv(label: str, value: Any, width: int = 32) -> None:
    if not _enabled():
        return

    safe_value = sanitize(value, key=label)
    label_text = f"{label:<{width}}"

    if _supports_color():
        print(f"{_style(label_text, _MAGENTA)}: {safe_value}")
    else:
        print(f"{label_text}: {safe_value}")


def info(text: str) -> None:
    if _enabled():
        print(_style(f"ℹ {text}", _BLUE))


def success(text: str) -> None:
    if _enabled():
        print(_style(f"✓ {text}", _GREEN))


def warning(text: str) -> None:
    if _enabled():
        print(_style(f"⚠ {text}", _YELLOW))


def error(text: str) -> None:
    if _enabled():
        print(_style(f"✗ {text}", _RED))


def soft_line(char: str = "─", width: int = 96) -> None:
    if _enabled():
        print(_style(char * width, _GRAY))


def json_block(title: str, value: Any) -> None:
    if not _enabled():
        return

    subsection(title)

    safe_value = sanitize(value)
    rendered = json.dumps(
        safe_value,
        indent=2,
        sort_keys=True,
        default=str,
        ensure_ascii=False,
    )

    print(_style(rendered, _GRAY))


def path_info(
    *,
    label: str,
    path: Path | str | None,
    check_exists: bool = True,
) -> None:
    if not _enabled():
        return

    if path is None:
        kv(label, "<none>")
        return

    checked_path = Path(path)
    kv(label, checked_path)

    if check_exists:
        kv(f"{label} exists", checked_path.exists())
        if checked_path.exists() and checked_path.is_file():
            kv(f"{label} size", checked_path.stat().st_size)


def model_identity(
    *,
    model_name: str,
    local_id: Any,
    portable_hash: str | None = None,
    node_key: str | None = None,
) -> None:
    subsection(f"{model_name} identity")
    kv("Local database ID", local_id)
    kv("Portable content hash", portable_hash or "<missing>")
    kv("Node key", node_key or "<not supplied>")


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
    kv("Source node", source_node_key)
    kv("Target node", target_node_key)
    kv("Resource hash", resource_hash)
    kv("Transfer mode", transfer_mode)

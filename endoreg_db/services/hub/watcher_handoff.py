from __future__ import annotations

import logging
import os
import stat
import time
from math import isfinite
from pathlib import Path

from endoreg_db.config.env import (
    get_watcher_poll_interval_seconds,
    get_watcher_stable_after_seconds,
)
from endoreg_db.utils.structured_logging import emit_structured_event, path_reference

logger = logging.getLogger(__name__)

WATCHER_SETTLE_TIMEOUT_SECONDS = 2.0
IN_PROGRESS_HANDOFF_SUFFIXES = (
    ".tmp",
    ".part",
    ".partial",
    ".crdownload",
    ".download",
)
IN_PROGRESS_HANDOFF_NAME_MARKERS = (".tmp.", ".part.")


class WatcherFileNotReadyError(RuntimeError):
    """Raised when a watcher source should be retried later."""


def _validated_duration_seconds(
    value: float,
    *,
    label: str,
    allow_zero: bool,
) -> float:
    parsed = float(value)
    valid_lower_bound = parsed >= 0 if allow_zero else parsed > 0
    if not isfinite(parsed) or not valid_lower_bound:
        expected = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{label} must be a finite {expected} duration")
    return parsed


def watcher_path_reference_text(file_path: Path) -> str:
    reference = path_reference(file_path)
    return (
        f"path_sha256={reference['path_sha256']} suffix={reference['suffix'] or 'none'}"
    )


def is_in_progress_handoff_path(file_path: Path | str) -> bool:
    name = Path(file_path).name.lower()
    return any(name.endswith(suffix) for suffix in IN_PROGRESS_HANDOFF_SUFFIXES) or any(
        marker in name for marker in IN_PROGRESS_HANDOFF_NAME_MARKERS
    )


def reject_in_progress_handoff_path(file_path: Path | str) -> None:
    file_path = Path(file_path)
    if is_in_progress_handoff_path(file_path):
        raise WatcherFileNotReadyError(
            "Watcher ingestion ignores in-progress handoff files. "
            "Atomically rename to the final media name before ingesting; "
            f"{watcher_path_reference_text(file_path)}"
        )


def watcher_file_stat(file_path: Path) -> os.stat_result:
    try:
        stat_result = file_path.lstat()
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Watcher file not found; {watcher_path_reference_text(file_path)}"
        )
    if stat.S_ISLNK(stat_result.st_mode):
        raise ValueError(
            "Watcher source must not be a symbolic link; "
            f"{watcher_path_reference_text(file_path)}"
        )
    if not stat.S_ISREG(stat_result.st_mode):
        raise ValueError(
            "Watcher path is not a regular file; "
            f"{watcher_path_reference_text(file_path)}"
        )
    return stat_result


def watcher_stat_snapshot(stat_result: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(stat_result.st_dev),
        int(stat_result.st_ino),
        int(stat_result.st_size),
        int(stat_result.st_mtime_ns),
    )


def wait_for_watcher_file_ready(
    file_path: Path | str,
    *,
    stable_after_seconds: float | None = None,
    poll_interval_seconds: float | None = None,
    timeout_seconds: float = WATCHER_SETTLE_TIMEOUT_SECONDS,
) -> os.stat_result:
    """
    Refuse temp handoff names and wait until a watcher source is stable.

    This protects direct service callers as well as the lx-annotate watcher
    scanner. Not-ready files stay in place so they can be retried later.
    """
    file_path = Path(file_path)
    reject_in_progress_handoff_path(file_path)

    stable_after = (
        get_watcher_stable_after_seconds()
        if stable_after_seconds is None
        else stable_after_seconds
    )
    poll_interval = (
        get_watcher_poll_interval_seconds()
        if poll_interval_seconds is None
        else poll_interval_seconds
    )
    stable_after = _validated_duration_seconds(
        stable_after,
        label="stable_after_seconds",
        allow_zero=True,
    )
    poll_interval = _validated_duration_seconds(
        poll_interval,
        label="poll_interval_seconds",
        allow_zero=False,
    )
    timeout = _validated_duration_seconds(
        timeout_seconds,
        label="timeout_seconds",
        allow_zero=False,
    )

    deadline = time.monotonic() + max(timeout, stable_after + poll_interval)
    stable_since: float | None = None
    previous_snapshot: tuple[int, int, int, int] | None = None
    previous_size: int | None = None

    while True:
        stat_result = watcher_file_stat(file_path)
        snapshot = watcher_stat_snapshot(stat_result)
        _device_id, _inode, size_bytes, mtime_ns = snapshot
        now = time.monotonic()

        if size_bytes > 0 and snapshot == previous_snapshot:
            if stable_since is None:
                stable_since = now
            if now - stable_since >= stable_after:
                return stat_result
        else:
            if previous_snapshot is not None and snapshot != previous_snapshot:
                emit_structured_event(
                    logger,
                    "watcher.file_changed_during_settle",
                    file=path_reference(file_path),
                    previous_size_bytes=previous_size,
                    current_size_bytes=size_bytes,
                    current_mtime_ns=mtime_ns,
                )
            previous_snapshot = snapshot
            previous_size = size_bytes
            stable_since = now if size_bytes > 0 else None

            if stable_after <= 0 and size_bytes > 0:
                return stat_result

        if now >= deadline:
            raise WatcherFileNotReadyError(
                "Watcher file did not become stable before ingest timeout; "
                f"{watcher_path_reference_text(file_path)}"
            )

        time.sleep(min(poll_interval, max(deadline - now, 0.01)))


def assert_watcher_file_unchanged(
    *,
    file_path: Path,
    expected_stat: os.stat_result,
    current_stat: os.stat_result,
    stage: str,
) -> None:
    expected_snapshot = watcher_stat_snapshot(expected_stat)
    current_snapshot = watcher_stat_snapshot(current_stat)
    if current_snapshot == expected_snapshot:
        return
    emit_structured_event(
        logger,
        "watcher.file_changed_after_settle",
        level=logging.WARNING,
        file=path_reference(file_path),
        stage=stage,
        expected_size_bytes=expected_snapshot[2],
        current_size_bytes=current_snapshot[2],
        expected_mtime_ns=expected_snapshot[3],
        current_mtime_ns=current_snapshot[3],
    )
    raise WatcherFileNotReadyError(
        "Watcher file changed after settle check; deferring ingestion; "
        f"{watcher_path_reference_text(file_path)}"
    )

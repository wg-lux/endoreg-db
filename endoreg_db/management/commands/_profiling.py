from __future__ import annotations

import cProfile
import io
import os
import pstats
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from django.core.management.base import CommandError, CommandParser

from endoreg_db.utils.file_operations import (
    atomic_move_file,
    atomic_write_file,
    ensure_directory,
    safe_unlink_file,
)

_T = TypeVar("_T")

_PROFILE_SORT_KEYS = frozenset(
    {
        "calls",
        "cumulative",
        "cumtime",
        "filename",
        "line",
        "module",
        "name",
        "ncalls",
        "nfl",
        "pcalls",
        "stdname",
        "time",
        "tottime",
    }
)


@dataclass(frozen=True)
class CommandProfilingConfig:
    output_path: Path | None
    summary_output_path: Path | None
    sort_by: str
    limit: int

    @property
    def enabled(self) -> bool:
        return self.output_path is not None or self.summary_output_path is not None


def add_profiling_arguments(parser: CommandParser) -> None:
    parser.add_argument(
        "--profile-output",
        default=None,
        help="Write binary cProfile stats for this command to the given path.",
    )
    parser.add_argument(
        "--profile-summary-output",
        default=None,
        help="Write a text pstats summary for this command to the given path.",
    )
    parser.add_argument(
        "--profile-sort",
        choices=sorted(_PROFILE_SORT_KEYS),
        default="cumulative",
        help="pstats sort key used for --profile-summary-output.",
    )
    parser.add_argument(
        "--profile-limit",
        type=int,
        default=40,
        help="Maximum number of pstats rows written to --profile-summary-output.",
    )


def command_profiling_config_from_options(
    options: Mapping[str, object],
) -> CommandProfilingConfig:
    limit = positive_int_option(options.get("profile_limit"), "--profile-limit")
    sort_by = _sort_key_option(options.get("profile_sort"))
    return CommandProfilingConfig(
        output_path=_optional_path(options.get("profile_output")),
        summary_output_path=_optional_path(options.get("profile_summary_output")),
        sort_by=sort_by,
        limit=limit,
    )


def profiling_metadata(config: CommandProfilingConfig) -> dict[str, object]:
    payload: dict[str, object] = {}
    if config.output_path is not None:
        payload["profile_output"] = str(config.output_path)
    if config.summary_output_path is not None:
        payload["profile_summary_output"] = str(config.summary_output_path)
        payload["profile_sort"] = config.sort_by
        payload["profile_limit"] = config.limit
    return payload


def run_with_optional_profile(
    work: Callable[[], _T],
    *,
    config: CommandProfilingConfig,
) -> _T:
    if not config.enabled:
        return work()

    profiler = cProfile.Profile()
    started_at = time.perf_counter()
    try:
        profiler.enable()
        return work()
    finally:
        profiler.disable()
        elapsed_seconds = time.perf_counter() - started_at
        if config.output_path is not None:
            _dump_profile(profiler, config.output_path)
        if config.summary_output_path is not None:
            _write_profile_summary(
                profiler=profiler,
                config=config,
                elapsed_seconds=elapsed_seconds,
            )


def _dump_profile(profiler: cProfile.Profile, destination: Path) -> None:
    destination = Path(destination)
    ensure_directory(destination.parent)
    temp_destination = destination.with_name(f"{destination.name}.tmp.{os.getpid()}")
    try:
        profiler.dump_stats(str(temp_destination))
        atomic_move_file(source=temp_destination, destination=destination)
    except Exception:
        safe_unlink_file(temp_destination, missing_ok=True)
        raise


def _write_profile_summary(
    *,
    profiler: cProfile.Profile,
    config: CommandProfilingConfig,
    elapsed_seconds: float,
) -> None:
    destination = config.summary_output_path
    if destination is None:
        return

    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream)
    stats.sort_stats(config.sort_by).print_stats(config.limit)
    profile_output = (
        str(config.output_path) if config.output_path is not None else "(not written)"
    )
    content = (
        f"elapsed_wall_seconds: {elapsed_seconds:.6f}\n"
        f"profile_output: {profile_output}\n"
        f"profile_sort: {config.sort_by}\n"
        f"profile_limit: {config.limit}\n\n"
        f"{stream.getvalue()}"
    ).encode("utf-8")
    atomic_write_file(
        destination=destination,
        content=(content,),
        required_bytes=len(content),
    )


def _optional_path(value: object) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(text)


def _sort_key_option(value: object) -> str:
    text = str(value or "cumulative").strip().lower()
    if text not in _PROFILE_SORT_KEYS:
        allowed = ", ".join(sorted(_PROFILE_SORT_KEYS))
        raise CommandError(f"--profile-sort must be one of: {allowed}")
    return text


def positive_int_option(value: object, label: str) -> int:
    try:
        result = int(str(value))
    except (TypeError, ValueError) as exc:
        raise CommandError(f"{label} must be a positive integer.") from exc
    if result <= 0:
        raise CommandError(f"{label} must be a positive integer.")
    return result

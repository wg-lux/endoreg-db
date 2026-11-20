from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from django.core.management.base import CommandError, CommandParser

from endoreg_db.utils.profiling import (
    ProfilingConfig as CommandProfilingConfig,
    run_with_optional_profile as run_with_optional_profile,
)

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

from __future__ import annotations

import cProfile
import io
import marshal
import logging
import pstats
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from uuid import uuid4
from typing import cast

from endoreg_db.utils.file_operations import atomic_write_file

logger = logging.getLogger(__name__)

_profile_directory: ContextVar[Path | None] = ContextVar(
    "function_profile_directory", default=None
)
_profile_active: ContextVar[bool] = ContextVar("function_profile_active", default=False)


@dataclass(frozen=True)
class ProfilingConfig:
    output_path: Path | None
    summary_output_path: Path | None
    sort_by: str
    limit: int
    cpu_time: bool = False

    @property
    def enabled(self) -> bool:
        return self.output_path is not None or self.summary_output_path is not None


def run_with_optional_profile[T](
    work: Callable[[], T],
    *,
    config: ProfilingConfig,
    strict_output: bool = True,
) -> T:
    if not config.enabled or _profile_active.get():
        return work()

    profiler = (
        cProfile.Profile(timer=time.thread_time)
        if config.cpu_time
        else cProfile.Profile()
    )
    token = _profile_active.set(True)
    started_at = time.perf_counter()
    work_failed = False
    try:
        profiler.enable()
        return work()
    except BaseException:
        work_failed = True
        raise
    finally:
        profiler.disable()
        _profile_active.reset(token)
        elapsed_seconds = time.perf_counter() - started_at
        try:
            if config.output_path is not None:
                _dump_profile(profiler, config.output_path)
            if config.summary_output_path is not None:
                _write_profile_summary(
                    profiler=profiler,
                    config=config,
                    elapsed_seconds=elapsed_seconds,
                )
        except Exception as exc:
            if strict_output and not work_failed:
                raise
            logger.error(
                "Execution profile could not be written (%s)", type(exc).__name__
            )


def _dump_profile(profiler: cProfile.Profile, destination: Path) -> None:
    # pstats uses this marshal payload, but its typeshed stub omits the attribute.
    stats = cast(dict[object, object], getattr(pstats.Stats(profiler), "stats"))
    content = marshal.dumps(stats)
    atomic_write_file(
        destination=destination, content=(content,), required_bytes=len(content)
    )


def _write_profile_summary(
    *,
    profiler: cProfile.Profile,
    config: ProfilingConfig,
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
        f"profile_clock: {'thread_cpu' if config.cpu_time else 'wall'}\n"
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


@contextmanager
def profile_functions(output_directory: Path) -> Generator[None]:
    """Opt in for decorated functions in this synchronous scope/current thread.

    Each outermost decorated call writes unique .prof and .txt files, including
    on failure. Nested calls are included in its call graph. This does not
    propagate to Celery workers or profile FFmpeg subprocess internals.
    """
    token = _profile_directory.set(output_directory)
    try:
        yield
    finally:
        _profile_directory.reset(token)


def profiled_function[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Preserve the function signature and bypass profiling unless opted in."""

    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        directory = _profile_directory.get()
        if directory is None or _profile_active.get():
            return function(*args, **kwargs)
        name = f"{function.__module__}.{function.__qualname__}-{uuid4().hex}"
        return run_with_optional_profile(
            lambda: function(*args, **kwargs),
            strict_output=False,
            config=ProfilingConfig(
                output_path=directory / f"{name}.prof",
                summary_output_path=directory / f"{name}.txt",
                sort_by="cumulative",
                limit=40,
                cpu_time=True,
            ),
        )

    return wrapped

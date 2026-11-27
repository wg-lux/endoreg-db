"""Stream pytest results to a durable log so interrupted runs keep their output.

The terminal output of a long run is often cut off (piped through ``tail``,
killed by a timeout, or stopped with Ctrl-C). This plugin, active only in the
controller process (never inside xdist workers), therefore:

* writes one flushed line per finished test to ``.pytest_cache/last-run.log``
  (override with ``--run-log PATH``), including failure tracebacks, so the file
  is complete up to the last finished test even after ``SIGKILL``;
* converts ``SIGTERM`` and ``SIGHUP`` into ``KeyboardInterrupt`` so pytest's
  regular interruption path stops workers and prints the short test summary;
* writes a final line with outcome counts and the exit status.

Follow a run with ``tail -f .pytest_cache/last-run.log``; list failures with
``grep -E '^(FAILED|ERROR)' .pytest_cache/last-run.log``.
"""

from __future__ import annotations

import signal
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import TextIO

import pytest

_DEFAULT_RELATIVE_PATH = Path(".pytest_cache") / "last-run.log"
_FORWARDED_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGTERM, signal.SIGHUP)

_SignalHandler = Callable[[int, FrameType | None], object] | int | None


class RunLog:
    """Line-buffered result log owned by the pytest controller process."""

    def __init__(self, path: Path) -> None:
        self.path: Path = path
        self.counts: Counter[str] = Counter()
        self.exitstatus: int | pytest.ExitCode = pytest.ExitCode.INTERRUPTED
        self._started: float = time.monotonic()
        self._previous_handlers: dict[signal.Signals, _SignalHandler] = {}
        path.parent.mkdir(parents=True, exist_ok=True)
        self._stream: TextIO = path.open("w", encoding="utf-8", buffering=1)

    def write(self, line: str) -> None:
        self._stream.write(line.rstrip("\n") + "\n")
        self._stream.flush()

    def record(
        self, outcome: str, label: str, duration: float, details: str = ""
    ) -> None:
        self.counts[outcome] += 1
        self.write(f"{outcome} {label} ({duration:.2f}s)")
        if details:
            self.write(details)

    def elapsed(self) -> float:
        return time.monotonic() - self._started

    def format_counts(self) -> str:
        if not self.counts:
            return "no results"
        return ", ".join(
            f"{count} {outcome}" for outcome, count in sorted(self.counts.items())
        )

    def install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        for signum in _FORWARDED_SIGNALS:
            self._previous_handlers[signum] = signal.signal(signum, self._on_signal)

    def restore_signal_handlers(self) -> None:
        for signum, handler in self._previous_handlers.items():
            signal.signal(signum, handler)
        self._previous_handlers.clear()

    def _on_signal(self, signum: int, _frame: FrameType | None) -> None:
        name = signal.Signals(signum).name
        self.write(
            f"INTERRUPTED by {name} after {self.elapsed():.1f}s; {self.format_counts()}"
        )
        # Restore the previous handlers first so a second signal terminates immediately.
        self.restore_signal_handlers()
        raise KeyboardInterrupt(name)

    def close(self) -> None:
        self.restore_signal_handlers()
        status = self.exitstatus
        label = status.name if isinstance(status, pytest.ExitCode) else str(status)
        self.write(
            f"FINISHED exit={label} after {self.elapsed():.1f}s; {self.format_counts()}"
        )
        self._stream.close()


_RUN_LOG_KEY = pytest.StashKey[RunLog]()
# Report hooks receive no config object, so the controller's log is also kept here.
_active_run_log: RunLog | None = None


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-log",
        dest="run_log",
        default=None,
        metavar="PATH",
        help="File receiving one flushed line per test result (default: .pytest_cache/last-run.log).",
    )


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
    global _active_run_log
    if hasattr(
        config, "workerinput"
    ):  # xdist worker: results are logged by the controller.
        return
    option: object = config.getoption("run_log")
    if option is not None and not isinstance(option, str):
        raise TypeError(f"--run-log must be a path string, got {type(option).__name__}")
    path = Path(option) if option else config.rootpath / _DEFAULT_RELATIVE_PATH
    run_log = RunLog(path)
    run_log.write(f"STARTED args={' '.join(config.invocation_params.args)}")
    run_log.install_signal_handlers()
    config.stash[_RUN_LOG_KEY] = run_log
    _active_run_log = run_log


def pytest_report_header(config: pytest.Config) -> str | None:
    run_log = config.stash.get(_RUN_LOG_KEY, None)
    return None if run_log is None else f"run log: {run_log.path}"


def pytest_collectreport(report: pytest.CollectReport) -> None:
    if _active_run_log is not None and report.failed:
        _active_run_log.record(
            "ERROR",
            f"{report.nodeid or '<session>'} [collect]",
            0.0,
            report.longreprtext,
        )


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    run_log = _active_run_log
    if run_log is None:
        return
    if report.failed:
        outcome = "FAILED" if report.when == "call" else "ERROR"
        run_log.record(
            outcome,
            f"{report.nodeid} [{report.when}]",
            report.duration,
            report.longreprtext,
        )
    elif report.when == "call":
        outcome = (
            "XFAIL"
            if report.skipped and hasattr(report, "wasxfail")
            else report.outcome.upper()
        )
        run_log.record(outcome, report.nodeid, report.duration)
    elif report.when == "setup" and report.skipped:
        run_log.record("SKIPPED", report.nodeid, report.duration)


def pytest_sessionfinish(
    session: pytest.Session, exitstatus: int | pytest.ExitCode
) -> None:
    run_log = session.config.stash.get(_RUN_LOG_KEY, None)
    if run_log is not None:
        run_log.exitstatus = exitstatus


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, config: pytest.Config
) -> None:
    run_log = config.stash.get(_RUN_LOG_KEY, None)
    if run_log is not None:
        terminalreporter.write_line(f"Per-test results: {run_log.path}")


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config: pytest.Config) -> None:
    global _active_run_log
    run_log = config.stash.get(_RUN_LOG_KEY, None)
    if run_log is None:
        return
    run_log.close()
    _active_run_log = None

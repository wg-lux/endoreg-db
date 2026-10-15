"""Exercise the deployed model boundary with Celery's real billiard pool."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

import pytest
from billiard.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded
from lx_anonymizer.model_loading import ModelBlock, ModelChain

pytestmark = pytest.mark.no_db


class _Result(Protocol):
    def get(self, timeout: float) -> object: ...


class _Pool(Protocol):
    def apply_async(
        self,
        func: Callable[..., object],
        args: tuple[object, ...] = (),
        *,
        soft_timeout: float | None = None,
        timeout: float | None = None,
    ) -> _Result: ...

    def terminate(self) -> None: ...

    def join(self) -> None: ...


class _PoolFactory(Protocol):
    def __call__(
        self, *, processes: int, maxtasksperchild: int, enable_timeouts: bool
    ) -> _Pool: ...


@dataclass(frozen=True)
class _ModelFactory:
    snapshot: str | None = None

    def __call__(self) -> Callable[[str], int]:
        if self.snapshot is not None:
            # A detached orphan escapes killpg(executor_pid), requiring the
            # model supervisor's subreaper cleanup after the task is killed.
            script = (
                "import subprocess,sys; "
                "p=subprocess.Popen([sys.executable,'-c',"
                "'import time; time.sleep(60)'],start_new_session=True); "
                "print(p.pid,flush=True)"
            )
            with subprocess.Popen(
                [sys.executable, "-c", script],
                start_new_session=True,
                stdout=subprocess.PIPE,
                text=True,
            ) as middle:
                assert middle.stdout is not None
                descendant = int(middle.stdout.readline())
                middle.wait(timeout=3)
            Path(self.snapshot).write_text(f"{os.getpid()} {descendant}")
        return _Model(self.snapshot is not None)


@dataclass(frozen=True)
class _Model:
    hang: bool

    def __call__(self, value: str) -> int:
        if self.hang:
            time.sleep(60)
        return os.getpid()


def _model_task(
    snapshot: str | None = None, ignore_soft: bool = False
) -> tuple[int, int]:
    if ignore_soft:
        signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    with ModelChain([ModelBlock("prefork-test", _ModelFactory(snapshot), 30)]) as chain:
        return os.getpid(), chain.run("nonclinical lifecycle probe")


def _assert_gone(pids: list[int]) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        # Zombies count as remaining: the supervisor must actually reap.
        if all(not Path(f"/proc/{pid}").exists() for pid in pids):
            return
        time.sleep(0.05)
    pytest.fail(f"Model descendants were not reaped: {pids}")


def _new_pool() -> _Pool:
    factory = cast(_PoolFactory, import_module("billiard.pool").Pool)
    return factory(processes=1, maxtasksperchild=1, enable_timeouts=True)


def _task_result(result: object) -> tuple[int, int]:
    assert isinstance(result, tuple)
    assert len(cast(tuple[object, ...], result)) == 2
    task_pid, model_pid = cast(tuple[object, object], result)
    assert isinstance(task_pid, int) and isinstance(model_pid, int)
    return task_pid, model_pid


def test_billiard_prefork_recycles_task_and_reaps_model() -> None:
    pool = _new_pool()
    try:
        first = _task_result(pool.apply_async(_model_task).get(timeout=15))
        second = _task_result(pool.apply_async(_model_task).get(timeout=15))
        assert first[0] != second[0]
        assert first[1] != second[1]
        _assert_gone([first[1], second[1]])
    finally:
        pool.terminate()
        pool.join()


@pytest.mark.parametrize("hard", [False, True], ids=["soft-deadline", "hard-deadline"])
def test_billiard_deadline_reaps_detached_models_and_accepts_next_task(
    tmp_path: Path, hard: bool
) -> None:
    snapshot = tmp_path / "model-processes"
    pool = _new_pool()
    try:
        result = pool.apply_async(
            _model_task,
            (str(snapshot), hard),
            soft_timeout=3,
            timeout=6,
        )
        if hard:
            wrapped_error = cast(
                type[Exception], import_module("billiard.einfo").ExceptionWithTraceback
            )
            with pytest.raises(wrapped_error) as raised:
                result.get(timeout=15)
            assert isinstance(getattr(raised.value, "exc", None), TimeLimitExceeded)
        else:
            with pytest.raises(SoftTimeLimitExceeded):
                result.get(timeout=15)
        assert snapshot.exists(), (
            "Deadline fired before model initialization was exercised"
        )
        pids = [int(value) for value in snapshot.read_text().split()]
        assert len(pids) == 2
        _assert_gone(pids)
        following = _task_result(pool.apply_async(_model_task).get(timeout=15))
        _assert_gone([following[1]])
    finally:
        pool.terminate()
        pool.join()

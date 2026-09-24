from __future__ import annotations

import pstats
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from endoreg_db.utils import profiling


@profiling.profiled_function
def profiled_leaf(value: int) -> int:
    return sum(range(value))


def test_disabled_profiling_does_not_construct_profiler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = Mock(side_effect=AssertionError("profiling must be opt-in"))
    monkeypatch.setattr(profiling.cProfile, "Profile", factory)
    assert profiled_leaf(5) == 10
    factory.assert_not_called()


@pytest.mark.parametrize("fail", [False, True])
def test_profile_captures_nested_calls_and_preserves_outcome(
    tmp_path: Path, fail: bool
) -> None:
    error = RuntimeError("private payload must not enter profile artifacts")

    @profiling.profiled_function
    def outer() -> int:
        result = profiled_leaf(5)
        if fail:
            raise error
        return result

    with profiling.profile_functions(tmp_path):
        if fail:
            with pytest.raises(RuntimeError) as caught:
                outer()
            assert caught.value is error
        else:
            assert outer() == 10
    profiles = list(tmp_path.glob("*.prof"))
    assert len(profiles) == 1
    names = set(pstats.Stats(str(profiles[0])).get_stats_profile().func_profiles)
    assert {"outer", "profiled_leaf"} <= names
    summaries = list(tmp_path.glob("*.txt"))
    assert len(summaries) == 1
    assert "cumulative" in summaries[0].read_text()
    assert "profile_clock: thread_cpu" in summaries[0].read_text()
    assert str(error) not in summaries[0].read_text()
    assert str(error).encode() not in profiles[0].read_bytes()
    profiled_leaf(5)
    assert list(tmp_path.glob("*.prof")) == profiles


def test_profiles_are_unique_and_do_not_cross_threads(tmp_path: Path) -> None:
    with profiling.profile_functions(tmp_path):
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(profiled_leaf, 5).result() == 10
        assert not list(tmp_path.iterdir())
        profiled_leaf(5)
        profiled_leaf(5)
    assert len(list(tmp_path.glob("*.prof"))) == 2


def test_command_profile_includes_decorated_functions_without_nested_profiler(
    tmp_path: Path,
) -> None:
    from endoreg_db.management.commands._profiling import (
        CommandProfilingConfig,
        run_with_optional_profile,
    )

    destination = tmp_path / "command.prof"
    with profiling.profile_functions(tmp_path):
        result = run_with_optional_profile(
            lambda: profiled_leaf(5),
            config=CommandProfilingConfig(destination, None, "cumulative", 40),
        )
    assert result == 10
    assert list(tmp_path.glob("*.prof")) == [destination]
    assert (
        "profiled_leaf"
        in pstats.Stats(str(destination)).get_stats_profile().func_profiles
    )


@pytest.mark.parametrize("fail", [False, True])
def test_profile_write_failure_preserves_work_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    fail: bool,
) -> None:
    monkeypatch.setattr(
        profiling, "atomic_write_file", Mock(side_effect=OSError("disk full"))
    )
    error = RuntimeError("original failure")

    @profiling.profiled_function
    def work() -> int:
        if fail:
            raise error
        return 42

    with profiling.profile_functions(tmp_path):
        if fail:
            with pytest.raises(RuntimeError) as caught:
                work()
            assert caught.value is error
        else:
            assert work() == 42
    assert "Execution profile could not be written" in caplog.text
    assert str(error) not in caplog.text


def test_video_import_profile_records_real_entrypoint_on_failure(
    tmp_path: Path,
) -> None:
    from endoreg_db.import_files.video_import_service import VideoImportService

    service = object.__new__(VideoImportService)
    with profiling.profile_functions(tmp_path / "profiles"):
        with pytest.raises(FileNotFoundError):
            service.import_and_anonymize(
                tmp_path / "missing.mp4", "test-center", "test-processor"
            )
    profile = next((tmp_path / "profiles").glob("*.prof"))
    assert (
        "_import_and_anonymize"
        in pstats.Stats(str(profile)).get_stats_profile().func_profiles
    )

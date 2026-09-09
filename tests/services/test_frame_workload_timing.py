from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn
import uuid

import pytest

from endoreg_db.models import Center, Frame, FrameExtractionRequest, VideoFile
from endoreg_db.services.jobs import frame_extraction_jobs
from endoreg_db.utils.workload_timing import (
    WorkloadOperation,
    WorkloadOutcome,
    WorkloadPhase,
    WorkloadQueue,
    WorkloadTaskFamily,
)

full_module = importlib.import_module(
    "endoreg_db.services.video_files._frames._extract_frames"
)
range_module = importlib.import_module(
    "endoreg_db.services.video_files._frames._manage_frame_range"
)

_SAFE_TIMING_KEYS = {
    "started_at",
    "operation",
    "phase",
    "outcome",
    "task_family",
    "queue",
}


def _capture_emissions(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
) -> list[dict[str, object]]:
    emissions: list[dict[str, object]] = []

    def capture(_logger: object, **dimensions: object) -> float:
        emissions.append(dimensions)
        return 1.0

    monkeypatch.setattr(module, "start_workload_timing", lambda: 10.0)
    monkeypatch.setattr(module, "emit_workload_timing", capture)
    return emissions


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (WorkloadOutcome.COMPLETED, WorkloadOutcome.COMPLETED),
        (WorkloadOutcome.REUSED, WorkloadOutcome.REUSED),
    ],
)
def test_full_frame_total_timing_uses_only_bounded_dimensions(
    monkeypatch: pytest.MonkeyPatch,
    outcome: WorkloadOutcome,
    expected: WorkloadOutcome,
) -> None:
    emissions = _capture_emissions(monkeypatch, full_module)

    def fake_impl(*_args: object, **_kwargs: object) -> tuple[bool, WorkloadOutcome]:
        return True, outcome

    monkeypatch.setattr(
        full_module,
        "_extract_frames_impl",
        fake_impl,
    )

    assert full_module._extract_frames(SimpleNamespace()) is True

    assert len(emissions) == 1
    assert set(emissions[0]) == _SAFE_TIMING_KEYS
    assert emissions[0] == {
        "started_at": 10.0,
        "operation": WorkloadOperation.FRAME_FULL_MATERIALIZATION,
        "phase": WorkloadPhase.TOTAL,
        "outcome": expected,
        "task_family": WorkloadTaskFamily.FRAME_EXTRACTION,
        "queue": WorkloadQueue.FRAME_EXTRACTION,
    }


def test_full_frame_total_timing_records_failure_without_exception_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emissions = _capture_emissions(monkeypatch, full_module)

    def fail(*_args: object, **_kwargs: object) -> NoReturn:
        raise RuntimeError("sensitive-path-or-identifier")

    monkeypatch.setattr(full_module, "_extract_frames_impl", fail)

    with pytest.raises(RuntimeError, match="sensitive-path-or-identifier"):
        full_module._extract_frames(SimpleNamespace())

    assert len(emissions) == 1
    assert set(emissions[0]) == _SAFE_TIMING_KEYS
    assert emissions[0]["phase"] == WorkloadPhase.TOTAL
    assert emissions[0]["outcome"] == WorkloadOutcome.FAILED
    assert "sensitive-path-or-identifier" not in repr(emissions[0])


@pytest.mark.parametrize(
    "outcome",
    [WorkloadOutcome.COMPLETED, WorkloadOutcome.REUSED],
)
def test_frame_range_total_timing_records_terminal_outcome(
    monkeypatch: pytest.MonkeyPatch,
    outcome: WorkloadOutcome,
) -> None:
    emissions = _capture_emissions(monkeypatch, range_module)

    def fake_impl(*_args: object, **_kwargs: object) -> tuple[bool, WorkloadOutcome]:
        return True, outcome

    monkeypatch.setattr(range_module, "_extract_frame_range_impl", fake_impl)

    assert range_module._extract_frame_range(SimpleNamespace(), 4, 6) is True
    assert emissions == [
        {
            "started_at": 10.0,
            "operation": WorkloadOperation.FRAME_RANGE_MATERIALIZATION,
            "phase": WorkloadPhase.TOTAL,
            "outcome": outcome,
            "task_family": WorkloadTaskFamily.FRAME_EXTRACTION,
            "queue": WorkloadQueue.FRAME_EXTRACTION,
        }
    ]


def test_full_frame_decode_failure_emits_no_sensitive_details(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    emissions = _capture_emissions(monkeypatch, full_module)

    def fail_extract(*_args: object, **_kwargs: object) -> NoReturn:
        raise RuntimeError("sensitive-source-path")

    monkeypatch.setattr(
        full_module,
        "extract_full_frame_set_to_directory",
        fail_extract,
    )

    with pytest.raises(RuntimeError, match="sensitive-source-path"):
        full_module._extract_verified_staged_manifest(
            SimpleNamespace(),
            staged_frame_dir=tmp_path,
            quality=2,
            ext="jpg",
            from_processed=False,
            expected_count=1,
        )

    assert len(emissions) == 1
    assert emissions[0]["phase"] == WorkloadPhase.DECODE
    assert emissions[0]["outcome"] == WorkloadOutcome.FAILED
    assert set(emissions[0]) == _SAFE_TIMING_KEYS
    assert "sensitive-source-path" not in repr(emissions[0])


def test_range_decode_and_publication_timing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    emissions = _capture_emissions(monkeypatch, range_module)
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    output_dir = tmp_path / "frames"

    def source_context(
        *_args: object, **_kwargs: object
    ) -> AbstractContextManager[Path]:
        return nullcontext(source_path)

    monkeypatch.setattr(range_module, "_video_source_context", source_context)

    def extract(
        _source: Path,
        staged_output_dir: Path,
        start_frame: int,
        end_frame: int,
        **_kwargs: object,
    ) -> list[Path]:
        paths: list[Path] = []
        for frame_number in range(start_frame, end_frame):
            path = staged_output_dir / f"frame_{frame_number:07d}.jpg"
            path.write_bytes(b"frame")
            paths.append(path)
        return paths

    monkeypatch.setattr(range_module, "ffmpeg_extract_frame_range", extract)

    paths = range_module.extract_frame_range_to_directory(
        SimpleNamespace(video_hash="must-not-be-emitted"),
        output_dir=output_dir,
        start_frame=4,
        end_frame=6,
    )

    assert len(paths) == 2
    assert [emission["phase"] for emission in emissions] == [
        WorkloadPhase.DECODE,
        WorkloadPhase.PUBLICATION,
    ]
    assert all(
        emission["outcome"] == WorkloadOutcome.COMPLETED
        and set(emission) == _SAFE_TIMING_KEYS
        and "must-not-be-emitted" not in repr(emission)
        for emission in emissions
    )


def test_range_publication_failure_is_timed_without_target_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    emissions = _capture_emissions(monkeypatch, range_module)
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")

    def source_context(
        *_args: object, **_kwargs: object
    ) -> AbstractContextManager[Path]:
        return nullcontext(source_path)

    def extract(
        _source: Path,
        staged_output_dir: Path,
        start_frame: int,
        _end_frame: int,
        **_kwargs: object,
    ) -> list[Path]:
        path = staged_output_dir / f"frame_{start_frame:07d}.jpg"
        path.write_bytes(b"frame")
        return [path]

    def fail_publish(**_kwargs: object) -> NoReturn:
        raise OSError("sensitive-target-path")

    monkeypatch.setattr(range_module, "_video_source_context", source_context)
    monkeypatch.setattr(range_module, "ffmpeg_extract_frame_range", extract)
    monkeypatch.setattr(range_module, "atomic_move_file", fail_publish)

    with pytest.raises(OSError, match="sensitive-target-path"):
        range_module.extract_frame_range_to_directory(
            SimpleNamespace(video_hash="must-not-be-emitted"),
            output_dir=tmp_path / "frames",
            start_frame=4,
            end_frame=5,
        )

    assert [emission["phase"] for emission in emissions] == [
        WorkloadPhase.DECODE,
        WorkloadPhase.PUBLICATION,
    ]
    assert emissions[-1]["outcome"] == WorkloadOutcome.FAILED
    assert all(
        set(emission) == _SAFE_TIMING_KEYS
        and "sensitive" not in repr(emission)
        and "must-not-be-emitted" not in repr(emission)
        for emission in emissions
    )


def _video_and_request(
    tmp_path: Path,
    *,
    request_status: str,
) -> tuple[VideoFile, FrameExtractionRequest]:
    center = Center.objects.create(name=f"timing-center-{uuid.uuid4().hex[:8]}")
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"timing-video-{uuid.uuid4().hex}",
        frame_count=10,
        frame_dir=str(tmp_path / "frames"),
    )
    request = FrameExtractionRequest.objects.create(
        video=video,
        frame_number=3,
        status=request_status,
        task_id="must-not-be-emitted",
    )
    return video, request


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("status", "result", "expected_outcome"),
    [
        (FrameExtractionRequest.STATUS_SUCCESS, True, WorkloadOutcome.REUSED),
        (FrameExtractionRequest.STATUS_RUNNING, False, WorkloadOutcome.DEFERRED),
    ],
)
def test_durable_frame_request_records_nonexecuting_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: str,
    result: bool,
    expected_outcome: WorkloadOutcome,
) -> None:
    emissions = _capture_emissions(monkeypatch, frame_extraction_jobs)
    video, request = _video_and_request(tmp_path, request_status=status)

    assert (
        frame_extraction_jobs.run_frame_extraction_request(
            request_id=request.pk,
            video_id=video.pk,
            frame_number=3,
        )
        is result
    )

    assert [emission["phase"] for emission in emissions] == [
        WorkloadPhase.DATABASE_STATE,
        WorkloadPhase.TOTAL,
    ]
    assert all(
        emission["outcome"] == expected_outcome
        and set(emission) == _SAFE_TIMING_KEYS
        and "must-not-be-emitted" not in repr(emission)
        for emission in emissions
    )


@pytest.mark.django_db
def test_durable_frame_request_records_completed_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    emissions = _capture_emissions(monkeypatch, frame_extraction_jobs)
    video, request = _video_and_request(
        tmp_path,
        request_status=FrameExtractionRequest.STATUS_PENDING,
    )
    frame_dir = Path(str(video.frame_dir))
    frame_dir.mkdir(parents=True)
    frame = Frame.objects.create(
        video=video,
        frame_number=3,
        relative_path="frame_0000003.jpg",
        is_extracted=False,
    )
    frame.file_path.write_bytes(b"frame")

    assert frame_extraction_jobs.run_frame_extraction_request(
        request_id=request.pk,
        video_id=video.pk,
        frame_number=3,
    )

    request.refresh_from_db()
    assert request.status == FrameExtractionRequest.STATUS_SUCCESS
    assert [emission["outcome"] for emission in emissions] == [
        WorkloadOutcome.COMPLETED,
        WorkloadOutcome.COMPLETED,
        WorkloadOutcome.COMPLETED,
    ]
    assert [emission["phase"] for emission in emissions] == [
        WorkloadPhase.DATABASE_STATE,
        WorkloadPhase.DATABASE_STATE,
        WorkloadPhase.TOTAL,
    ]


@pytest.mark.django_db
def test_durable_frame_request_records_failed_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    emissions = _capture_emissions(monkeypatch, frame_extraction_jobs)
    video, request = _video_and_request(
        tmp_path,
        request_status=FrameExtractionRequest.STATUS_PENDING,
    )

    def fail_extract(*_args: object, **_kwargs: object) -> NoReturn:
        raise RuntimeError("sensitive-extraction-detail")

    monkeypatch.setattr(
        frame_extraction_jobs,
        "extract_video_frame_range",
        fail_extract,
    )

    with pytest.raises(RuntimeError, match="sensitive-extraction-detail"):
        frame_extraction_jobs.run_frame_extraction_request(
            request_id=request.pk,
            video_id=video.pk,
            frame_number=3,
        )

    request.refresh_from_db()
    assert request.status == FrameExtractionRequest.STATUS_FAILURE
    assert emissions[-2]["phase"] == WorkloadPhase.DATABASE_STATE
    assert emissions[-2]["outcome"] == WorkloadOutcome.FAILED
    assert emissions[-1]["phase"] == WorkloadPhase.TOTAL
    assert emissions[-1]["outcome"] == WorkloadOutcome.FAILED
    assert all(
        set(emission) == _SAFE_TIMING_KEYS
        and "sensitive-extraction-detail" not in repr(emission)
        for emission in emissions
    )

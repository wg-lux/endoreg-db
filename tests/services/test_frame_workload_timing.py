from __future__ import annotations

from pathlib import Path
from typing import NoReturn
import uuid

import pytest

from endoreg_db.models import Center, Frame, FrameExtractionRequest, VideoFile
from endoreg_db.services.jobs import frame_extraction_jobs
from endoreg_db.utils.workload_timing import (
    WorkloadOutcome,
    WorkloadPhase,
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


def _video_and_request(
    tmp_path: Path,
    *,
    request_status: str,
) -> tuple[VideoFile, FrameExtractionRequest]:
    center = Center.objects.create(name=f"timing-center-{uuid.uuid4().hex[:8]}")
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash=f"timing-video-{uuid.uuid4().hex}",
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
    frame.file_path.parent.mkdir(parents=True, exist_ok=True)
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

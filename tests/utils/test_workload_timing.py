from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import cast

import pytest
from _pytest.logging import LogCaptureFixture

from endoreg_db.utils.workload_timing import (
    WORKLOAD_DURATION_METRIC,
    WORKLOAD_TIMING_EVENT,
    WorkloadOperation,
    WorkloadOutcome,
    WorkloadPhase,
    WorkloadQueue,
    WorkloadRetryBucket,
    WorkloadTaskFamily,
    emit_workload_timing,
    normalize_task_family,
    normalize_workload_operation,
    normalize_workload_outcome,
    normalize_workload_phase,
    normalize_workload_queue,
    retry_bucket,
    start_workload_timing,
)


def test_workload_timing_emits_only_bounded_dimensions_and_duration(
    caplog: LogCaptureFixture,
) -> None:
    logger = logging.getLogger("tests.workload_timing")
    started_at = start_workload_timing(clock=lambda: 10.0)

    with caplog.at_level(logging.INFO, logger=logger.name):
        duration = emit_workload_timing(
            logger,
            started_at=started_at,
            operation=WorkloadOperation.HLS_MATERIALIZATION,
            phase=WorkloadPhase.ENCODE,
            outcome=WorkloadOutcome.COMPLETED,
            task_family=WorkloadTaskFamily.VIDEO_HLS_MATERIALIZATION,
            queue=WorkloadQueue.FFMPEG_MEDIA,
            retry=WorkloadRetryBucket.ZERO,
            clock=lambda: 12.75,
        )

    assert duration == 2.75
    payload = cast(
        Mapping[str, object], getattr(caplog.records[-1], "structured_event")
    )
    assert payload == {
        "event": WORKLOAD_TIMING_EVENT,
        "operation": "hls_materialization",
        "phase": "encode",
        "outcome": "completed",
        "task_family": "video_hls_materialization",
        "queue": "ffmpeg_media",
        "retry_bucket": "0",
        "duration_seconds": 2.75,
        "metric_name": WORKLOAD_DURATION_METRIC,
        "metric_value": 2.75,
    }
    forbidden_keys = {
        "task_id",
        "args",
        "kwargs",
        "path",
        "hash",
        "exception",
    }
    assert forbidden_keys.isdisjoint(payload)


def test_workload_timing_normalizes_unknown_dimensions() -> None:
    assert normalize_workload_operation("new-operation") is WorkloadOperation.UNKNOWN
    assert normalize_workload_phase("decode") is WorkloadPhase.DECODE
    assert normalize_workload_phase("new-phase") is WorkloadPhase.UNKNOWN
    assert normalize_workload_outcome("new-outcome") is WorkloadOutcome.UNKNOWN
    assert normalize_task_family("third.party.task") is WorkloadTaskFamily.UNKNOWN
    assert normalize_workload_queue("tenant-specific-queue") is WorkloadQueue.UNKNOWN
    assert retry_bucket("2") is WorkloadRetryBucket.UNKNOWN
    assert retry_bucket(-1) is WorkloadRetryBucket.UNKNOWN


@pytest.mark.parametrize(
    ("retry_count", "expected"),
    [
        (0, WorkloadRetryBucket.ZERO),
        (1, WorkloadRetryBucket.ONE),
        (2, WorkloadRetryBucket.TWO_TO_THREE),
        (3, WorkloadRetryBucket.TWO_TO_THREE),
        (4, WorkloadRetryBucket.FOUR_OR_MORE),
        (100, WorkloadRetryBucket.FOUR_OR_MORE),
    ],
)
def test_retry_bucket_is_bounded(
    retry_count: int,
    expected: WorkloadRetryBucket,
) -> None:
    assert retry_bucket(retry_count) is expected


def test_workload_timing_rejects_invalid_clock_values(
    caplog: LogCaptureFixture,
) -> None:
    logger = logging.getLogger("tests.workload_timing")

    with pytest.raises(ValueError, match="finite value"):
        start_workload_timing(clock=lambda: float("inf"))
    with pytest.raises(ValueError, match="non-negative"):
        emit_workload_timing(
            logger,
            started_at=2.0,
            operation=WorkloadOperation.CELERY_TASK,
            outcome=WorkloadOutcome.COMPLETED,
            clock=lambda: 1.0,
        )

    assert not caplog.records

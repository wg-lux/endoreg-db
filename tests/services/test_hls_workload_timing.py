# pyright: reportPrivateUsage=false
from __future__ import annotations

from collections.abc import Callable
from unittest.mock import Mock

import pytest

from endoreg_db.exceptions import MediaOperationDeferred
from endoreg_db.services import hls_media
from endoreg_db.services.video_storage_normalization import (
    VideoStorageNormalizationError,
)
from endoreg_db.utils.workload_timing import (
    WorkloadOperation,
    WorkloadOutcome,
    WorkloadPhase,
    WorkloadQueue,
    WorkloadRetryBucket,
    WorkloadTaskFamily,
)


def _result(status: str) -> hls_media.HlsMaterializationResult:
    return hls_media.HlsMaterializationResult(
        video_id=17,
        artifact_kind="processed",
        status=status,
        key_id="private-key-id",
        playlist_relative_path="private/playlist.m3u8",
        segment_directory_relative_path="private/segments",
        segment_count=2,
    )


@pytest.mark.parametrize(
    ("status", "expected_outcome"),
    [
        ("materialized", WorkloadOutcome.COMPLETED),
        ("already_ready", WorkloadOutcome.REUSED),
        ("already_materializing", WorkloadOutcome.DEFERRED),
        ("failed_validation", WorkloadOutcome.VALIDATION_FAILED),
    ],
)
def test_materialize_hls_emits_one_bounded_total_outcome(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected_outcome: WorkloadOutcome,
) -> None:
    timing = Mock()
    monkeypatch.setattr(hls_media, "start_workload_timing", lambda: 10.0)
    monkeypatch.setattr(hls_media, "emit_workload_timing", timing)

    def result_with_status(
        *args: object,
        **kwargs: object,
    ) -> hls_media.HlsMaterializationResult:
        del args, kwargs
        return _result(status)

    monkeypatch.setattr(hls_media, "_materialize_video_hls_impl", result_with_status)

    result = hls_media.materialize_video_hls(17)

    assert result.status == status
    timing.assert_called_once_with(
        hls_media.logger,
        started_at=10.0,
        operation=WorkloadOperation.HLS_MATERIALIZATION,
        phase=WorkloadPhase.TOTAL,
        outcome=expected_outcome,
        task_family=WorkloadTaskFamily.VIDEO_HLS_MATERIALIZATION,
        queue=WorkloadQueue.FFMPEG_MEDIA,
        retry=WorkloadRetryBucket.UNKNOWN,
    )
    assert {
        "video_id",
        "artifact_kind",
        "encoder_backend",
        "key_id",
        "path",
        "hash",
        "task_id",
        "exception",
    }.isdisjoint(timing.call_args.kwargs)


@pytest.mark.parametrize(
    ("error_factory", "expected_outcome"),
    [
        (
            lambda: MediaOperationDeferred("active media lease"),
            WorkloadOutcome.DEFERRED,
        ),
        (
            lambda: VideoStorageNormalizationError("invalid output"),
            WorkloadOutcome.VALIDATION_FAILED,
        ),
        (lambda: RuntimeError("encoder failed"), WorkloadOutcome.FAILED),
    ],
)
def test_materialize_hls_emits_total_outcome_when_work_raises(
    monkeypatch: pytest.MonkeyPatch,
    error_factory: Callable[[], BaseException],
    expected_outcome: WorkloadOutcome,
) -> None:
    timing = Mock()
    error = error_factory()
    monkeypatch.setattr(hls_media, "start_workload_timing", lambda: 20.0)
    monkeypatch.setattr(hls_media, "emit_workload_timing", timing)

    def raise_error(
        *args: object, **kwargs: object
    ) -> hls_media.HlsMaterializationResult:
        raise error

    monkeypatch.setattr(hls_media, "_materialize_video_hls_impl", raise_error)

    with pytest.raises(type(error), match=str(error)):
        hls_media.materialize_video_hls(17)

    assert timing.call_args.kwargs["phase"] is WorkloadPhase.TOTAL
    assert timing.call_args.kwargs["outcome"] is expected_outcome


@pytest.mark.parametrize(
    ("error", "expected_outcome"),
    [
        (None, WorkloadOutcome.COMPLETED),
        (
            VideoStorageNormalizationError("invalid source"),
            WorkloadOutcome.VALIDATION_FAILED,
        ),
        (RuntimeError("preflight failed"), WorkloadOutcome.FAILED),
    ],
)
def test_hls_phase_timer_preserves_outcome_and_uses_fixed_dimensions(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException | None,
    expected_outcome: WorkloadOutcome,
) -> None:
    timing = Mock()
    monkeypatch.setattr(hls_media, "start_workload_timing", lambda: 30.0)
    monkeypatch.setattr(hls_media, "emit_workload_timing", timing)

    if error is None:
        with hls_media._timed_hls_phase(WorkloadPhase.ENCODER_PREFLIGHT):
            pass
    else:
        with pytest.raises(type(error), match=str(error)):
            with hls_media._timed_hls_phase(WorkloadPhase.ENCODER_PREFLIGHT):
                raise error

    timing.assert_called_once_with(
        hls_media.logger,
        started_at=30.0,
        operation=WorkloadOperation.HLS_MATERIALIZATION,
        phase=WorkloadPhase.ENCODER_PREFLIGHT,
        outcome=expected_outcome,
        task_family=WorkloadTaskFamily.VIDEO_HLS_MATERIALIZATION,
        queue=WorkloadQueue.FFMPEG_MEDIA,
        retry=WorkloadRetryBucket.UNKNOWN,
    )

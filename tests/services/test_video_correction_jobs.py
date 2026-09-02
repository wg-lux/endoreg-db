# pyright: reportUnknownArgumentType=false, reportUnknownLambdaType=false
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from collections.abc import Generator

import pytest

from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
import endoreg_db.services.jobs.video_correction_jobs as video_correction_jobs
from endoreg_db.services.jobs.heavy_jobs import HeavyJobKind, queue_for_job_kind


class _History:
    def __init__(self, config: dict[str, object]) -> None:
        self.status = VideoProcessingHistory.STATUS_PENDING
        self.config = config
        self.output_file = ""
        self.success: dict[str, object] | None = None
        self.failure = ""

    def mark_running(self) -> None:
        self.status = VideoProcessingHistory.STATUS_RUNNING

    def mark_success(self, **kwargs: object) -> None:
        self.status = VideoProcessingHistory.STATUS_SUCCESS
        self.success = kwargs

    def mark_failure(self, error_message: str) -> None:
        self.status = VideoProcessingHistory.STATUS_FAILURE
        self.failure = error_message


def _job_config() -> video_correction_jobs.VideoAnonymizationCorrectionJobConfig:
    return video_correction_jobs.VideoAnonymizationCorrectionJobConfig.model_validate(
        {
            "strategy": "processor_region",
            "processing_method": "streaming",
            "region": {
                "mode": "device",
                "device_name": "olympus_cv_1500",
            },
            "human_review_required": True,
            "apply_all_frames": True,
            "queue": "ffmpeg_media",
        }
    )


def test_video_anonymization_correction_routes_to_ffmpeg_worker() -> None:
    assert (
        queue_for_job_kind(HeavyJobKind.VIDEO_ANONYMIZATION_CORRECTION)
        == "ffmpeg_media"
    )


def test_video_anonymization_job_config_rejects_unreviewed_output() -> None:
    payload = _job_config().model_dump(mode="json")
    payload["human_review_required"] = False

    with pytest.raises(ValueError, match="human_review_required"):
        video_correction_jobs.VideoAnonymizationCorrectionJobConfig.model_validate(
            payload
        )


def test_correction_job_marks_success_only_after_processed_hls_is_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    history = _History(_job_config().model_dump(mode="json"))
    raw_file = SimpleNamespace(name="raw/source.mp4")
    video = SimpleNamespace(pk=11, video_hash="hash-11", raw_file=raw_file)
    events: list[str] = []

    monkeypatch.setattr(
        video_correction_jobs.VideoProcessingHistory.objects,
        "get",
        lambda **_kwargs: history,
    )
    monkeypatch.setattr(
        video_correction_jobs.VideoFile.objects,
        "get",
        lambda **_kwargs: video,
    )
    monkeypatch.setattr(
        video_correction_jobs,
        "defer_if_video_media_busy",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        video_correction_jobs,
        "_output_path",
        lambda *_args: tmp_path / "corrected.mp4",
    )

    @contextmanager
    def _local_file(_field_file: object) -> Generator[Path, None, None]:
        yield tmp_path / "raw.mp4"

    monkeypatch.setattr(video_correction_jobs, "ensure_local_file", _local_file)
    monkeypatch.setattr(video_correction_jobs, "FrameCleaner", lambda: object())
    monkeypatch.setattr(
        video_correction_jobs,
        "apply_video_anonymization_strategy",
        lambda **_kwargs: {"frames_processed": 100},
    )
    monkeypatch.setattr(
        video_correction_jobs,
        "_promote_output",
        lambda _temp, final: final,
    )
    monkeypatch.setattr(
        video_correction_jobs,
        "update_processed_file",
        lambda *_args: "processed/corrected.mp4",
    )

    def _materialize(*_args: object, **kwargs: object) -> SimpleNamespace:
        events.append(f"hls:{kwargs['artifact_kind']}:{kwargs['force']}")
        return SimpleNamespace(status="materialized", segment_count=8)

    monkeypatch.setattr(video_correction_jobs, "materialize_video_hls", _materialize)

    result = video_correction_jobs.run_video_anonymization_correction(11, 27)

    assert events == ["hls:processed:True"]
    assert result["status"] == "success"
    assert history.success is not None
    assert history.success["output_file"] == "processed/corrected.mp4"


def test_correction_job_fails_when_processed_hls_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    history = _History(_job_config().model_dump(mode="json"))
    video = SimpleNamespace(
        pk=11,
        video_hash="hash-11",
        raw_file=SimpleNamespace(name="raw/source.mp4"),
    )
    monkeypatch.setattr(
        video_correction_jobs.VideoProcessingHistory.objects,
        "get",
        lambda **_kwargs: history,
    )
    monkeypatch.setattr(
        video_correction_jobs.VideoFile.objects,
        "get",
        lambda **_kwargs: video,
    )
    monkeypatch.setattr(
        video_correction_jobs,
        "defer_if_video_media_busy",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        video_correction_jobs,
        "_output_path",
        lambda *_args: tmp_path / "corrected.mp4",
    )

    @contextmanager
    def _local_file(_field_file: object) -> Generator[Path, None, None]:
        yield tmp_path / "raw.mp4"

    monkeypatch.setattr(video_correction_jobs, "ensure_local_file", _local_file)
    monkeypatch.setattr(video_correction_jobs, "FrameCleaner", lambda: object())
    monkeypatch.setattr(
        video_correction_jobs,
        "apply_video_anonymization_strategy",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        video_correction_jobs,
        "_promote_output",
        lambda _temp, final: final,
    )
    monkeypatch.setattr(
        video_correction_jobs,
        "update_processed_file",
        lambda *_args: "processed/corrected.mp4",
    )
    monkeypatch.setattr(
        video_correction_jobs,
        "materialize_video_hls",
        lambda *_args, **_kwargs: SimpleNamespace(status="failed", segment_count=0),
    )

    with pytest.raises(RuntimeError, match="Processed HLS materialization"):
        video_correction_jobs.run_video_anonymization_correction(11, 27)

    assert history.success is None
    assert "Processed HLS materialization" in history.failure

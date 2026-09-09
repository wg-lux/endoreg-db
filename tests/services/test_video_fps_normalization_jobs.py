# pyright: reportPrivateUsage=false
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from django.test import override_settings

from endoreg_db.models import Center, VideoFile, VideoProcessingHistory
from endoreg_db.services.jobs import video_fps_normalization_jobs as jobs
from endoreg_db.services.jobs.heavy_jobs import HeavyJobKind


@dataclass(frozen=True)
class _ChangedResult:
    changed: bool = True
    detail: str = ""


@pytest.mark.django_db
def test_normalization_status_uses_persisted_fps_without_probing_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    center = Center.objects.create(
        name="fps-status-center",
        display_name="FPS Status Center",
    )
    video = VideoFile.objects.create(
        center=center,
        video_hash="fps-status-video",
        fps=25.0,
        duration=10.0,
        frame_count=250,
    )

    def fail_media_probe(_video: VideoFile) -> float:
        raise AssertionError("normalization status must not probe or stage media")

    monkeypatch.setattr(jobs, "get_video_fps", fail_media_probe)

    result = jobs.normalization_status(video)

    assert result.status == "ready"
    assert result.fps == 25.0


@pytest.mark.django_db
def test_normalization_status_rejects_invalid_persisted_fps() -> None:
    center = Center.objects.create(
        name="fps-invalid-status-center",
        display_name="FPS Invalid Status Center",
    )
    video = VideoFile.objects.create(
        center=center,
        video_hash="fps-invalid-status-video",
        fps=None,
        duration=10.0,
        frame_count=250,
    )

    with pytest.raises(ValueError, match="persisted FPS"):
        jobs.normalization_status(video)


@pytest.mark.django_db
def test_fps_normalization_job_requests_explicit_pre_annotation_downsampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    center = Center.objects.create(
        name="fps-job-center",
        display_name="FPS Job Center",
    )
    video = VideoFile.objects.create(
        center=center,
        video_hash="fps-job-video",
        fps=60.0,
        duration=10.0,
        frame_count=600,
    )
    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        config={"operation": jobs.CONFIG_OPERATION, "max_fps": 50.0, "queue": "video"},
    )
    captured: dict[str, object] = {}

    def fake_transcode(
        selected_video: VideoFile,
        **kwargs: object,
    ) -> _ChangedResult:
        captured.update(kwargs)
        selected_video.fps = 50.0
        selected_video.duration = 10.0
        selected_video.frame_count = 500
        selected_video.save(
            update_fields=["fps", "duration", "frame_count", "date_modified"]
        )
        return _ChangedResult()

    monkeypatch.setattr(
        jobs,
        "transcode_processed_video_for_storage_pressure",
        fake_transcode,
    )

    assert jobs._run_video_fps_normalization(video.pk, history.pk) is True

    assert captured == {
        "apply": True,
        "quality_mode": "quality",
        "allow_larger": True,
        "resample_max_fps": jobs.MAX_SEGMENTATION_FPS,
    }
    history.refresh_from_db()
    assert history.status == VideoProcessingHistory.STATUS_SUCCESS
    assert "normalized to 50 fps" in history.details


@pytest.mark.django_db
@override_settings(CELERY_TASK_ALWAYS_EAGER=False)
def test_mock_video_above_50_fps_exposes_full_normalization_status_lifecycle(
    mock_video_file: VideoFile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = mock_video_file
    video.fps = 60.0
    video.duration = 10.0
    video.frame_count = 600
    video.save(update_fields=["fps", "duration", "frame_count", "date_modified"])

    def fail_media_probe(_video: VideoFile) -> float:
        raise AssertionError("status and dispatch must not probe or stage media")

    monkeypatch.setattr(jobs, "get_video_fps", fail_media_probe)

    def fake_queue_for_job_kind(_kind: HeavyJobKind) -> str:
        return "ffmpeg-media"

    def fake_transport_gate(_kind: HeavyJobKind) -> None:
        return None

    monkeypatch.setattr(jobs, "queue_for_job_kind", fake_queue_for_job_kind)
    monkeypatch.setattr(
        jobs,
        "ensure_secure_transport_for_job_kind",
        fake_transport_gate,
    )

    from endoreg_db import tasks

    def fake_apply_async(*args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(id="mock-high-fps-task")

    monkeypatch.setattr(
        tasks.run_video_fps_normalization_task,
        "apply_async",
        fake_apply_async,
    )

    observed_statuses = [jobs.normalization_status(video).status]
    queued = jobs.dispatch_video_fps_normalization(video)
    observed_statuses.append(queued.status)
    assert queued.history_id is not None

    history = VideoProcessingHistory.objects.get(pk=queued.history_id)
    history.mark_running()
    observed_statuses.append(jobs.normalization_status(video).status)

    history.mark_failure("controlled mock transcode failure")
    failed = jobs.normalization_status(video)
    observed_statuses.append(failed.status)
    assert failed.detail == "controlled mock transcode failure"

    video.fps = jobs.MAX_SEGMENTATION_FPS
    video.frame_count = 500
    video.save(update_fields=["fps", "frame_count", "date_modified"])
    observed_statuses.append(jobs.normalization_status(video).status)

    assert observed_statuses == [
        "required",
        "queued",
        "running",
        "failed",
        "ready",
    ]

# pyright: reportPrivateUsage=false
from __future__ import annotations

from dataclasses import dataclass

import pytest

from endoreg_db.models import Center, VideoFile, VideoProcessingHistory
from endoreg_db.services.jobs import video_fps_normalization_jobs as jobs


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
        raw_video_hash="fps-status-video",
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
        raw_video_hash="fps-invalid-status-video",
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
        raw_video_hash="fps-job-video",
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
def test_video_above_50_fps_is_ready_without_queueing_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    center = Center.objects.create(
        name="fps-high-status-center",
        display_name="FPS High Status Center",
    )
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash="fps-high-status-video",
        fps=59.8,
        duration=10.0,
        frame_count=598,
    )

    def fail_media_probe(_video: VideoFile) -> float:
        raise AssertionError("status and dispatch must not probe or stage media")

    monkeypatch.setattr(jobs, "get_video_fps", fail_media_probe)

    status = jobs.normalization_status(video)
    dispatched = jobs.dispatch_video_fps_normalization(video)

    assert status.status == "ready"
    assert dispatched.status == "ready"
    assert dispatched.fps == 59.8
    assert dispatched.history_id is None
    assert not VideoProcessingHistory.objects.filter(video=video).exists()

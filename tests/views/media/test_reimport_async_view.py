from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from endoreg_db.models import Center, VideoFile, VideoProcessingHistory, VideoState
from endoreg_db.services.video_import import VideoImportService

pytestmark = pytest.mark.django_db


@pytest.fixture
def center():
    return Center.objects.create(name=f"reimport-view-center-{uuid4().hex}")


def _make_source_record(center: Center) -> VideoFile:
    token = uuid4().hex
    state = VideoState.objects.create(
        frames_extracted=False,
        frames_initialized=False,
        frame_count=1,
    )
    return VideoFile.objects.create(
        center=center,
        state=state,
        video_hash=f"reimport-view-{token}",
        original_file_name=f"reimport-view-{token}.mp4",
        raw_file=f"raw/reimport-view-{token}.mp4",
    )


def test_reimport_endpoint_queues_without_inline_import(
    monkeypatch,
    client,
    center,
):
    source_record = _make_source_record(center)

    def apply_async(*_args, **_kwargs):
        return SimpleNamespace(id="queued-view-task")

    monkeypatch.setenv("VIDEO_REIMPORT_JOB_MODE", "celery")
    monkeypatch.setattr(
        "endoreg_db.tasks.run_video_reimport_task.apply_async",
        apply_async,
    )

    with patch.object(
        VideoImportService,
        "import_and_anonymize",
        side_effect=AssertionError("VideoImportService ran inline"),
    ):
        response = client.post(
            f"/api/media/videos/{source_record.pk}/reimport/",
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["task_id"] == "queued-view-task"
    assert payload["queue"] == "ffmpeg_media"
    history = VideoProcessingHistory.objects.get(pk=payload["history_id"])
    assert history.status == VideoProcessingHistory.STATUS_PENDING

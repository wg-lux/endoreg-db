from __future__ import annotations

import shutil
import uuid
from typing import Any

import pytest
from django.test import TestCase

from endoreg_db.models import Center, Frame, FrameExtractionRequest, VideoFile
from endoreg_db.services.jobs import frame_extraction_jobs
from endoreg_db.utils.paths import protected_media_root


class FrameExtractionJobsTest(TestCase):
    def setUp(self) -> None:
        self.center = Center.objects.create(
            name=f"frame-extraction-center-{uuid.uuid4().hex[:8]}"
        )
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash=f"frame-extraction-video-{uuid.uuid4().hex}",
            frame_count=20,
            original_file_name="frame_extraction_test.mp4",
        )
        self.frame_dir = (
            protected_media_root() / f"pytest_frame_extraction_{uuid.uuid4().hex}"
        )
        self.frame_dir.mkdir(parents=True, exist_ok=True)
        self.video.frame_dir = str(self.frame_dir)
        self.video.save(update_fields=["frame_dir"])

    def tearDown(self) -> None:
        shutil.rmtree(self.frame_dir, ignore_errors=True)

    def test_request_frame_extraction_dispatches_to_frame_extraction_queue(self) -> None:
        calls: list[dict[str, object]] = []

        class _FakeAsyncResult:
            id = "queued-task-1"

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            frame_extraction_jobs,
            "_ensure_frame_extraction_broker_transport_allowed",
            lambda: None,
        )
        def fake_apply_async(**kwargs: object) -> _FakeAsyncResult:
            calls.append(dict(kwargs))
            return _FakeAsyncResult()

        monkeypatches.setattr(
            "endoreg_db.tasks.run_frame_extraction_request_task.apply_async",
            fake_apply_async,
        )
        try:
            result = frame_extraction_jobs.request_frame_extraction(
                video=self.video,
                frame_number=7,
            )
        finally:
            monkeypatches.undo()

        assert result.status == frame_extraction_jobs.REQUEST_STATUS_QUEUED
        assert (
            calls[0]["queue"]
            == frame_extraction_jobs.get_celery_frame_extraction_queue()
        )
        assert (
            calls[0]["routing_key"]
            == frame_extraction_jobs.get_celery_frame_extraction_queue()
        )
        request = FrameExtractionRequest.objects.get(
            video=self.video,
            frame_number=7,
        )
        assert request.task_id == "queued-task-1"
        assert request.status == FrameExtractionRequest.STATUS_PENDING

    def test_request_frame_extraction_reuses_active_request(self) -> None:
        request = FrameExtractionRequest.objects.create(
            video=self.video,
            frame_number=8,
            status=FrameExtractionRequest.STATUS_RUNNING,
            task_id="running-task",
        )

        result = frame_extraction_jobs.request_frame_extraction(
            video=self.video,
            frame_number=8,
        )

        assert result.status == frame_extraction_jobs.REQUEST_STATUS_RUNNING
        assert result.task_id == "running-task"
        assert FrameExtractionRequest.objects.count() == 1
        request.refresh_from_db()
        assert request.task_id == "running-task"

    def test_run_frame_extraction_request_marks_success_and_frame_extracted(self) -> None:
        request = FrameExtractionRequest.objects.create(
            video=self.video,
            frame_number=9,
            status=FrameExtractionRequest.STATUS_PENDING,
            task_id="task-9",
        )
        frame = Frame.objects.create(
            video=self.video,
            frame_number=9,
            relative_path="frame_0000009.jpg",
            is_extracted=False,
        )
        target_path = frame.file_path
        target_path.parent.mkdir(parents=True, exist_ok=True)

        def _fake_extract_video_frame_range(
            video_self: VideoFile,
            start_frame: int,
            end_frame: int,
            overwrite: bool = False,
            **kwargs: Any,
        ) -> bool:
            assert video_self.pk == self.video.pk
            assert start_frame == 9
            assert end_frame == 10
            assert overwrite is False
            target_path.write_bytes(b"\xff\xd8\xff\xdbframe")
            Frame.objects.filter(pk=frame.pk).update(is_extracted=True)
            return True

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            frame_extraction_jobs,
            "extract_video_frame_range",
            _fake_extract_video_frame_range,
        )
        try:
            result = frame_extraction_jobs.run_frame_extraction_request(
                request_id=request.pk,
                video_id=self.video.pk,
                frame_number=9,
            )
        finally:
            monkeypatches.undo()

        assert result is True
        request.refresh_from_db()
        frame.refresh_from_db()
        assert request.status == FrameExtractionRequest.STATUS_SUCCESS
        assert frame.is_extracted is True
        assert target_path.exists()

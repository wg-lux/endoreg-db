from __future__ import annotations

import importlib
import shutil
import uuid
import importlib.util
from pathlib import Path

import pytest
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from endoreg_db.models import Center, Frame, FrameExtractionRequest, VideoFile
from endoreg_db.utils.filesystem.paths import protected_media_root


class FrameStreamViewTests(TestCase):
    def setUp(self):
        self.center = Center.objects.create(
            name=f"frame-stream-center-{uuid.uuid4().hex[:8]}"
        )
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash=f"frame-stream-video-{uuid.uuid4().hex}",
            frame_count=100,
            original_file_name="frame_stream_test.mp4",
        )

        self.frame_dir = (
            protected_media_root() / f"pytest_frame_stream_{uuid.uuid4().hex}"
        )
        self.frame_dir.mkdir(parents=True, exist_ok=True)
        self.video.frame_dir = str(self.frame_dir)
        self.video.save(update_fields=["frame_dir"])

        self.frame = Frame.objects.create(
            video=self.video,
            frame_number=7,
            relative_path="frame_0000007.jpg",
            is_extracted=False,
        )

    def tearDown(self):
        shutil.rmtree(self.frame_dir, ignore_errors=True)

    def test_frame_stream_serves_existing_frame_and_nginx_offload(self):
        module_path = (
            Path(__file__).resolve().parents[3]
            / "endoreg_db"
            / "views"
            / "media"
            / "frame_media.py"
        )
        spec = importlib.util.spec_from_file_location(
            "test_frame_media_module", module_path
        )
        assert spec is not None and spec.loader is not None
        frame_media_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(frame_media_module)

        target_path = self.frame.file_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"\xff\xd8\xff\xdbfakejpg")
        Frame.objects.filter(pk=self.frame.pk).update(is_extracted=True)

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setenv("SERVE_WITH_NGINX", "true")
        monkeypatches.setenv(
            "DJANGO_CORS_ALLOWED_ORIGINS",
            "http://frontend.test",
        )
        monkeypatches.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
        try:
            factory = APIRequestFactory()
            req = factory.get(
                f"/api/media/videos/{self.video.pk}/frames/{self.frame.frame_number}/stream/"
            )
            view = frame_media_module.FrameStreamView.as_view()
            resp = view(
                req, video_id=self.video.pk, frame_number=self.frame.frame_number
            )
        finally:
            monkeypatches.undo()

        if resp.status_code != 200:
            body = getattr(resp, "data", None) or getattr(resp, "content", b"")
            raise AssertionError(f"Expected 200, got {resp.status_code}. body={body!r}")
        assert resp.status_code == 200
        self.frame.refresh_from_db()
        assert self.frame.is_extracted is True
        assert target_path.exists()

        assert resp["Content-Type"] == "image/jpeg"
        assert resp["X-Accel-Redirect"].startswith("/protected_media/")
        assert "frame_0000007.jpg" in resp["Content-Disposition"]
        assert resp["X-Accel-Buffering"] == "no"
        assert resp["Access-Control-Allow-Origin"] == "http://frontend.test"

    def test_frame_stream_queues_async_extraction_when_frame_missing(self):
        module_path = (
            Path(__file__).resolve().parents[3]
            / "endoreg_db"
            / "views"
            / "media"
            / "frame_media.py"
        )
        spec = importlib.util.spec_from_file_location(
            "test_frame_media_pending_module", module_path
        )
        assert spec is not None and spec.loader is not None
        frame_media_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(frame_media_module)

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            frame_media_module,
            "request_frame_extraction",
            lambda **kwargs: frame_media_module.FrameExtractionDispatchResult(
                request_id=17,
                task_id="task-17",
                status="queued",
                video_id=self.video.pk,
                frame_number=self.frame.frame_number,
            ),
        )
        try:
            factory = APIRequestFactory()
            req = factory.get(
                f"/api/media/videos/{self.video.pk}/frames/{self.frame.frame_number}/stream/"
            )
            view = frame_media_module.FrameStreamView.as_view()
            resp = view(
                req, video_id=self.video.pk, frame_number=self.frame.frame_number
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code == 202
        assert resp.data["status"] == "frame_extraction_pending"
        assert resp.data["request_id"] == 17
        assert resp.data["task_id"] == "task-17"

    def test_frame_stream_reports_failed_extraction_request(self):
        module_path = (
            Path(__file__).resolve().parents[3]
            / "endoreg_db"
            / "views"
            / "media"
            / "frame_media.py"
        )
        spec = importlib.util.spec_from_file_location(
            "test_frame_media_failed_module", module_path
        )
        assert spec is not None and spec.loader is not None
        frame_media_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(frame_media_module)

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            frame_media_module,
            "request_frame_extraction",
            lambda **kwargs: frame_media_module.FrameExtractionDispatchResult(
                request_id=18,
                task_id="task-18",
                status="failed",
                video_id=self.video.pk,
                frame_number=self.frame.frame_number,
            ),
        )
        try:
            factory = APIRequestFactory()
            req = factory.get(
                f"/api/media/videos/{self.video.pk}/frames/{self.frame.frame_number}/stream/"
            )
            view = frame_media_module.FrameStreamView.as_view()
            resp = view(
                req, video_id=self.video.pk, frame_number=self.frame.frame_number
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code == 409
        assert resp.data["status"] == "frame_extraction_failed"
        assert resp.data["request_id"] == 18

    def test_frame_stream_rejects_out_of_range_frame_number(self):
        factory = APIRequestFactory()
        req = factory.get(
            f"/api/media/videos/{self.video.pk}/frames/{self.video.frame_count}/stream/"
        )
        from endoreg_db.views.media.frame_media import FrameStreamView

        view = FrameStreamView.as_view()
        resp = view(req, video_id=self.video.pk, frame_number=self.video.frame_count)
        assert resp.status_code == 404

    def test_frame_stream_rejects_path_outside_video_frame_dir(self):
        escaped_target = protected_media_root() / f"frame_escape_{uuid.uuid4().hex}.jpg"
        escaped_target.write_bytes(b"\xff\xd8\xff\xdbfakejpg")
        try:
            self.frame.relative_path = f"../{escaped_target.name}"
            self.frame.is_extracted = True
            self.frame.save(update_fields=["relative_path", "is_extracted"])

            factory = APIRequestFactory()
            req = factory.get(
                f"/api/media/videos/{self.video.pk}/frames/{self.frame.frame_number}/stream/"
            )
            from endoreg_db.views.media.frame_media import FrameStreamView

            view = FrameStreamView.as_view()
            resp = view(
                req, video_id=self.video.pk, frame_number=self.frame.frame_number
            )
            assert resp.status_code == 404
        finally:
            escaped_target.unlink(missing_ok=True)

    def test_frame_stream_requires_auth_in_production_mode(self):
        frame_media_module = importlib.import_module(
            "endoreg_db.views.media.frame_media"
        )
        authz_permissions_module = importlib.import_module(
            "endoreg_db.authz.permissions"
        )
        util_permissions_module = importlib.import_module(
            "endoreg_db.utils.web.permissions"
        )

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(util_permissions_module, "is_debug_mode", lambda: False)
        monkeypatches.setattr(authz_permissions_module, "is_debug_mode", lambda: False)
        try:
            factory = APIRequestFactory()
            req = factory.get(
                f"/api/media/videos/{self.video.pk}/frames/{self.frame.frame_number}/stream/"
            )
            view = frame_media_module.FrameStreamView.as_view()
            resp = view(
                req, video_id=self.video.pk, frame_number=self.frame.frame_number
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code in {401, 403}

    def test_frame_stream_does_not_create_duplicate_request_rows_for_same_frame(self):
        request = FrameExtractionRequest.objects.create(
            video=self.video,
            frame_number=self.frame.frame_number,
            status=FrameExtractionRequest.STATUS_PENDING,
            task_id="existing-task",
        )
        factory = APIRequestFactory()
        req = factory.get(
            f"/api/media/videos/{self.video.pk}/frames/{self.frame.frame_number}/stream/"
        )
        from endoreg_db.views.media.frame_media import FrameStreamView

        view = FrameStreamView.as_view()
        resp = view(req, video_id=self.video.pk, frame_number=self.frame.frame_number)

        assert resp.status_code == 202
        assert FrameExtractionRequest.objects.count() == 1
        request.refresh_from_db()
        assert request.task_id == "existing-task"

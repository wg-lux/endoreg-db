from __future__ import annotations

# pyright: reportUnknownMemberType=false

import importlib
import importlib.util
import json
import shutil
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from endoreg_db.models import Center, Frame, FrameExtractionRequest, VideoFile
from endoreg_db.utils.paths import protected_media_root


def _load_frame_media_module(module_name: str) -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[3]
        / "endoreg_db"
        / "views"
        / "media"
        / "frame_media.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FrameStreamViewTests(TestCase):
    def setUp(self) -> None:
        self.center = Center.objects.create(
            name=f"frame-stream-center-{uuid.uuid4().hex[:8]}",
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

    def tearDown(self) -> None:
        shutil.rmtree(self.frame_dir, ignore_errors=True)

    def test_frame_stream_serves_existing_frame_and_nginx_offload(self) -> None:
        frame_media_module = _load_frame_media_module("test_frame_media_module")

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
                f"/api/media/videos/{self.video.pk}/frames/"
                f"{self.frame.frame_number}/stream/",
            )
            view = frame_media_module.FrameStreamView.as_view()
            resp = view(
                req,
                video_id=self.video.pk,
                frame_number=self.frame.frame_number,
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

    def test_frame_stream_queues_async_extraction_when_frame_missing(self) -> None:
        frame_media_module = _load_frame_media_module("test_frame_media_pending_module")

        def fake_request_frame_extraction(**kwargs: object) -> object:
            return frame_media_module.FrameExtractionDispatchResult(
                request_id=17,
                task_id="task-17",
                status="queued",
                video_id=self.video.pk,
                frame_number=self.frame.frame_number,
            )

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            frame_media_module,
            "request_frame_extraction",
            fake_request_frame_extraction,
        )

        try:
            factory = APIRequestFactory()
            req = factory.get(
                f"/api/media/videos/{self.video.pk}/frames/"
                f"{self.frame.frame_number}/stream/",
            )
            view = frame_media_module.FrameStreamView.as_view()
            resp = view(
                req,
                video_id=self.video.pk,
                frame_number=self.frame.frame_number,
            )
        finally:
            monkeypatches.undo()
        data = json.loads(resp.content)

        assert resp.status_code == 202
        assert data["status"] == "frame_extraction_pending"
        assert data["request_id"] == 17
        assert data["task_id"] == "task-17"

    def test_frame_stream_reports_failed_extraction_request(self) -> None:
        frame_media_module = _load_frame_media_module("test_frame_media_failed_module")

        def fake_failed_request_frame_extraction(**kwargs: object) -> object:
            return frame_media_module.FrameExtractionDispatchResult(
                request_id=18,
                task_id="task-18",
                status="failed",
                video_id=self.video.pk,
                frame_number=self.frame.frame_number,
            )

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            frame_media_module,
            "request_frame_extraction",
            fake_failed_request_frame_extraction,
        )

        try:
            factory = APIRequestFactory()
            req = factory.get(
                f"/api/media/videos/{self.video.pk}/frames/"
                f"{self.frame.frame_number}/stream/",
            )
            view = frame_media_module.FrameStreamView.as_view()
            resp = view(
                req,
                video_id=self.video.pk,
                frame_number=self.frame.frame_number,
            )
        finally:
            monkeypatches.undo()

        data = json.loads(resp.content)
        assert resp.status_code == 409
        assert data["status"] == "frame_extraction_failed"
        assert data["request_id"] == 18

    def test_frame_stream_rejects_out_of_range_frame_number(self) -> None:
        factory = APIRequestFactory()
        req = factory.get(
            f"/api/media/videos/{self.video.pk}/frames/"
            f"{self.video.frame_count}/stream/",
        )

        from endoreg_db.views.media.frame_media import FrameStreamView

        view = FrameStreamView.as_view()
        resp = view(
            req,
            video_id=self.video.pk,
            frame_number=self.video.frame_count,
        )
        assert resp.status_code == 404

    def test_frame_stream_rejects_path_outside_video_frame_dir(self) -> None:
        escaped_target = protected_media_root() / f"frame_escape_{uuid.uuid4().hex}.jpg"
        escaped_target.write_bytes(b"\xff\xd8\xff\xdbfakejpg")

        try:
            self.frame.relative_path = f"../{escaped_target.name}"
            self.frame.is_extracted = True
            self.frame.save(update_fields=["relative_path", "is_extracted"])

            factory = APIRequestFactory()
            req = factory.get(
                f"/api/media/videos/{self.video.pk}/frames/"
                f"{self.frame.frame_number}/stream/",
            )

            from endoreg_db.views.media.frame_media import FrameStreamView

            view = FrameStreamView.as_view()
            resp = view(
                req,
                video_id=self.video.pk,
                frame_number=self.frame.frame_number,
            )
            assert resp.status_code == 404
        finally:
            escaped_target.unlink(missing_ok=True)

    def test_frame_stream_requires_auth_in_production_mode(self) -> None:
        frame_media_module = importlib.import_module(
            "endoreg_db.views.media.frame_media",
        )
        authz_permissions_module = importlib.import_module(
            "endoreg_db.authz.permissions",
        )
        util_permissions_module = importlib.import_module(
            "endoreg_db.utils.permissions",
        )

        def fake_is_debug_mode() -> bool:
            return False

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            util_permissions_module,
            "is_debug_mode",
            fake_is_debug_mode,
        )
        monkeypatches.setattr(
            authz_permissions_module,
            "is_debug_mode",
            fake_is_debug_mode,
        )

        try:
            factory = APIRequestFactory()
            req = factory.get(
                f"/api/media/videos/{self.video.pk}/frames/"
                f"{self.frame.frame_number}/stream/",
            )
            view = frame_media_module.FrameStreamView.as_view()
            resp = view(
                req,
                video_id=self.video.pk,
                frame_number=self.frame.frame_number,
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code in {401, 403}

    def test_frame_stream_does_not_create_duplicate_request_rows_for_same_frame(
        self,
    ) -> None:
        request = FrameExtractionRequest.objects.create(
            video=self.video,
            frame_number=self.frame.frame_number,
            status=FrameExtractionRequest.STATUS_PENDING,
            task_id="existing-task",
        )
        factory = APIRequestFactory()
        req = factory.get(
            f"/api/media/videos/{self.video.pk}/frames/"
            f"{self.frame.frame_number}/stream/",
        )

        from endoreg_db.views.media.frame_media import FrameStreamView

        view = FrameStreamView.as_view()
        resp = view(
            req,
            video_id=self.video.pk,
            frame_number=self.frame.frame_number,
        )

        assert resp.status_code == 202
        assert FrameExtractionRequest.objects.count() == 1
        request.refresh_from_db()
        assert request.task_id == "existing-task"

    def test_decoded_frame_stream_serves_single_decoded_frame(self) -> None:
        from endoreg_db.views.media import frame_media as frame_media_module

        self.video.raw_file.save(
            "videos/raw_frame_stream_test.mp4",
            ContentFile(b"fake raw video"),
            save=True,
        )
        self.video.save(update_fields=["raw_file"])

        def fake_read_video_file_frame_jpeg(
            *args: object,
            **kwargs: object,
        ) -> object:
            frame_number = kwargs["frame_number"]
            assert isinstance(frame_number, int)

            return SimpleNamespace(
                frame_number=frame_number,
                timestamp=1.25,
                content_type="image/jpeg",
                image_bytes=b"\xff\xd8encoded-jpeg",
            )

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            frame_media_module,
            "read_video_file_frame_jpeg",
            fake_read_video_file_frame_jpeg,
        )

        try:
            factory = APIRequestFactory()
            req = factory.get(
                f"/api/media/videos/{self.video.pk}/frames/"
                f"{self.frame.frame_number}/decoded-stream/?file_type=raw",
            )
            user = User.objects.create_user(
                username="raw-frame-operator",
                is_staff=True,
            )
            force_authenticate(req, user=user)
            view = frame_media_module.DecodedFrameStreamView.as_view()
            resp = view(
                req,
                video_id=self.video.pk,
                frame_number=self.frame.frame_number,
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/jpeg"
        assert resp["X-Frame-File-Type"] == "raw"
        assert resp["X-Frame-Number"] == str(self.frame.frame_number)
        assert resp.content == b"\xff\xd8encoded-jpeg"

    def test_decoded_frame_stream_rejects_invalid_file_type(self) -> None:
        from endoreg_db.views.media.frame_media import DecodedFrameStreamView

        factory = APIRequestFactory()
        req = factory.get(
            f"/api/media/videos/{self.video.pk}/frames/"
            f"{self.frame.frame_number}/decoded-stream/?file_type=preview",
        )
        view = DecodedFrameStreamView.as_view()
        resp = view(
            req,
            video_id=self.video.pk,
            frame_number=self.frame.frame_number,
        )
        data = json.loads(resp.content)

        assert resp.status_code == 400
        assert "file_type" in data["error"]

    def test_decoded_frame_stream_reports_decode_failure(self) -> None:
        from endoreg_db.views.media import frame_media as frame_media_module

        self.video.processed_file.save(
            "videos/processed_frame_stream_test.mp4",
            ContentFile(b"fake processed video"),
            save=True,
        )
        state = self.video.get_or_create_state()
        state.anonymized = True
        state.save(update_fields=["anonymized"])
        self.video.save(update_fields=["processed_file"])

        def fake_failing_read_video_file_frame_jpeg(
            *args: object,
            **kwargs: object,
        ) -> object:
            raise RuntimeError("decode failed")

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            frame_media_module,
            "read_video_file_frame_jpeg",
            fake_failing_read_video_file_frame_jpeg,
        )

        try:
            factory = APIRequestFactory()
            req = factory.get(
                f"/api/media/videos/{self.video.pk}/frames/"
                f"{self.frame.frame_number}/decoded-stream/?file_type=processed",
            )
            user = User.objects.create_user(
                username="processed-frame-operator",
                is_staff=True,
            )
            force_authenticate(req, user=user)
            view = frame_media_module.DecodedFrameStreamView.as_view()
            resp = view(
                req,
                video_id=self.video.pk,
                frame_number=self.frame.frame_number,
            )
        finally:
            monkeypatches.undo()

        data = json.loads(resp.content)

        assert resp.status_code == 409
        assert data["status"] == "frame_decode_failed"
        assert data["file_type"] == "processed"

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_decoded_raw_frame_stream_rejects_video_outside_center_scope(self) -> None:
        from endoreg_db.views import access_control
        from endoreg_db.views.media import frame_media as frame_media_module

        self.video.raw_file.save(
            "videos/raw_frame_stream_wrong_center.mp4",
            ContentFile(b"fake raw video"),
            save=True,
        )

        def resolve_other_center(_user: object) -> int:
            return int(self.center.pk) + 1

        def fail_raw_frame_decode(*args: object, **kwargs: object) -> object:
            _ = args, kwargs
            pytest.fail("raw frame decode must not run")

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            access_control,
            "resolve_allowed_center_id",
            resolve_other_center,
        )
        monkeypatches.setattr(
            frame_media_module,
            "read_video_file_frame_jpeg",
            fail_raw_frame_decode,
        )

        try:
            request = APIRequestFactory().get(
                f"/api/media/videos/{self.video.pk}/frames/"
                f"{self.frame.frame_number}/decoded-stream/?file_type=raw"
            )
            user = User.objects.create_user(username="wrong-center-raw-frame-reader")
            force_authenticate(request, user=user)
            response = frame_media_module.DecodedFrameStreamView.as_view()(
                request,
                video_id=self.video.pk,
                frame_number=self.frame.frame_number,
            )
        finally:
            monkeypatches.undo()

        assert response.status_code == 403

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_decoded_processed_frame_stream_is_available_on_central_hub(
        self,
    ) -> None:
        from endoreg_db.views import access_control
        from endoreg_db.views.media import frame_media as frame_media_module

        self.video.processed_file.save(
            "videos/processed_frame_stream_cross_center.mp4",
            ContentFile(b"fake processed video"),
            save=True,
        )
        state = self.video.get_or_create_state()
        state.anonymized = True
        state.save(update_fields=["anonymized"])

        def resolve_other_center(_user: object) -> int:
            return int(self.center.pk) + 1

        def read_processed_frame(*args: object, **kwargs: object) -> object:
            _ = args
            frame_number = kwargs["frame_number"]
            assert isinstance(frame_number, int)
            return SimpleNamespace(
                frame_number=frame_number,
                timestamp=1.25,
                content_type="image/jpeg",
                image_bytes=b"\xff\xd8processed-jpeg",
            )

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            access_control,
            "resolve_allowed_center_id",
            resolve_other_center,
        )
        monkeypatches.setattr(
            frame_media_module,
            "read_video_file_frame_jpeg",
            read_processed_frame,
        )

        try:
            request = APIRequestFactory().get(
                f"/api/media/videos/{self.video.pk}/frames/"
                f"{self.frame.frame_number}/decoded-stream/?file_type=processed"
            )
            user = User.objects.create_user(username="centerless-hub-frame-reader")
            force_authenticate(request, user=user)
            response = frame_media_module.DecodedFrameStreamView.as_view()(
                request,
                video_id=self.video.pk,
                frame_number=self.frame.frame_number,
            )
        finally:
            monkeypatches.undo()

        assert response.status_code == 200
        assert response.content == b"\xff\xd8processed-jpeg"

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
    def test_decoded_processed_frame_stream_is_center_scoped_outside_hub(
        self,
    ) -> None:
        from endoreg_db.views.media import frame_media as frame_media_module

        self.video.processed_file.save(
            "videos/processed_frame_stream_non_hub.mp4",
            ContentFile(b"fake processed video"),
            save=True,
        )

        def fail_processed_frame_decode(*args: object, **kwargs: object) -> object:
            _ = args, kwargs
            pytest.fail("processed frame decode must not run")

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            frame_media_module,
            "read_video_file_frame_jpeg",
            fail_processed_frame_decode,
        )
        try:
            request = APIRequestFactory().get(
                f"/api/media/videos/{self.video.pk}/frames/"
                f"{self.frame.frame_number}/decoded-stream/?file_type=processed"
            )
            user = User.objects.create_user(username="centerless-non-hub-frame-reader")
            force_authenticate(request, user=user)
            response = frame_media_module.DecodedFrameStreamView.as_view()(
                request,
                video_id=self.video.pk,
                frame_number=self.frame.frame_number,
            )
        finally:
            monkeypatches.undo()

        assert response.status_code == 403

from __future__ import annotations

import importlib
import shutil
import uuid
import importlib.util
from pathlib import Path

import pytest
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from endoreg_db.models import Center, Frame, VideoFile
from endoreg_db.utils.paths import protected_media_root


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

    def test_frame_stream_on_demand_extract_and_nginx_offload(self):
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
        assert not target_path.exists()
        calls: list[tuple[int, int, bool]] = []

        def _fake_extract_specific_frame_range(
            video_self, start_frame, end_frame, overwrite=False, **kwargs
        ):
            calls.append((start_frame, end_frame, overwrite))
            assert video_self.pk == self.video.pk
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(b"\xff\xd8\xff\xdbfakejpg")
            Frame.objects.filter(pk=self.frame.pk).update(is_extracted=True)
            return True

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setenv("SERVE_WITH_NGINX", "true")
        monkeypatches.setenv("FRONTEND_ORIGIN", "http://frontend.test")
        monkeypatches.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
        monkeypatches.setattr(
            frame_media_module.VideoFile,
            "extract_specific_frame_range",
            _fake_extract_specific_frame_range,
        )
        monkeypatches.setattr(
            frame_media_module.VideoFile,
            "extract_frames",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError(
                    "full extraction fallback should not be used in this test"
                )
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

        if resp.status_code != 200:
            body = getattr(resp, "data", None) or getattr(resp, "content", b"")
            raise AssertionError(f"Expected 200, got {resp.status_code}. body={body!r}")
        assert resp.status_code == 200
        assert calls == [(7, 8, False)]
        self.frame.refresh_from_db()
        assert self.frame.is_extracted is True
        assert target_path.exists()

        assert resp["Content-Type"] == "image/jpeg"
        assert resp["X-Accel-Redirect"].startswith("/protected_media/")
        assert "frame_0000007.jpg" in resp["Content-Disposition"]
        assert resp["X-Accel-Buffering"] == "no"
        assert resp["Access-Control-Allow-Origin"] == "http://frontend.test"

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
            "endoreg_db.utils.permissions"
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

    def test_frame_stream_does_not_fallback_to_full_extraction(self):
        module_path = (
            Path(__file__).resolve().parents[3]
            / "endoreg_db"
            / "views"
            / "media"
            / "frame_media.py"
        )
        spec = importlib.util.spec_from_file_location(
            "test_frame_media_no_full_fallback", module_path
        )
        assert spec is not None and spec.loader is not None
        frame_media_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(frame_media_module)

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            frame_media_module.VideoFile,
            "extract_specific_frame_range",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("range extraction failed")
            ),
        )
        monkeypatches.setattr(
            frame_media_module.VideoFile,
            "extract_frames",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("full extraction fallback must not be called")
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

        assert resp.status_code == 404

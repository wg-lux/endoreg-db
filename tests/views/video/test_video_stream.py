from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.test import TestCase

from endoreg_db.utils.paths import STORAGE_DIR


class FakeStorage:
    def __init__(self, payload: bytes):
        self.payload = payload

    def get_plaintext_size(self, name: str) -> int:
        return len(self.payload)

    def iter_decrypted_range(self, name: str, *, start: int, end: int, chunk_size: int):
        selected = self.payload[start : end + 1]
        for offset in range(0, len(selected), chunk_size):
            yield selected[offset : offset + chunk_size]


class StubFieldFile:
    def __init__(self, storage, name: str):
        self.storage = storage
        self.name = name

    @property
    def size(self):
        return self.storage.get_plaintext_size(self.name)


class VideoStreamViewTests(TestCase):
    def test_video_stream_nginx_headers(self):
        from endoreg_db.views.video import video_stream as view_module

        storage_dir = STORAGE_DIR.resolve()
        storage_dir.mkdir(parents=True, exist_ok=True)
        tmp_file_path = None
        streamable_path = None
        monkeypatches = pytest.MonkeyPatch()
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".mp4", dir=storage_dir, delete=False
            ) as tmp:
                tmp.write(b"\x00\x00\x00\x18ftypmp42")
                tmp.flush()
                tmp_file_path = Path(tmp.name)

            streamable_path = storage_dir / "streamable_videos" / "raw" / "test.mp4"
            streamable_path.parent.mkdir(parents=True, exist_ok=True)
            streamable_path.write_bytes(b"\x00\x00\x00\x18ftypmp42")

            relative_name = tmp_file_path.relative_to(storage_dir).as_posix()
            fake_file_field = SimpleNamespace(
                name=relative_name,
                size=tmp_file_path.stat().st_size,
            )
            fake_video_obj = SimpleNamespace(
                active_raw_file=fake_file_field,
                processed_file=fake_file_field,
                storage_mode=view_module.VideoFile.StorageMode.FS_ENCRYPTED_STREAMABLE,
                streamable_relative_path="streamable_videos/raw/test.mp4",
                processed_streamable_relative_path="streamable_videos/processed/test.mp4",
            )

            monkeypatches.setenv("SERVE_WITH_NGINX", "true")
            monkeypatches.setenv("FRONTEND_ORIGIN", "http://frontend.test")
            monkeypatches.setattr(
                view_module, "NGINX_PROTECTED_URL", "/protected_media/"
            )
            monkeypatches.setattr(
                view_module.VideoFile.objects, "get", lambda **kwargs: fake_video_obj
            )

            response = self.client.get("/api/media/videos/123/stream/?type=raw")
        finally:
            monkeypatches.undo()
            if tmp_file_path and tmp_file_path.exists():
                tmp_file_path.unlink(missing_ok=True)
            if streamable_path and streamable_path.exists():
                streamable_path.unlink(missing_ok=True)

        assert response.status_code == 200
        assert "X-Accel-Redirect" in response
        assert (
            response["X-Accel-Redirect"]
            == "/protected_media/streamable_videos/raw/test.mp4"
        )
        assert response["X-Accel-Buffering"] == "no"
        assert response["Access-Control-Allow-Origin"] == "http://frontend.test"

    def test_video_stream_range_uses_storage_api_hooks(self):
        from endoreg_db.views.video import video_stream as view_module

        payload = (b"frame-" * 2048) + b"tail"
        fake_storage = FakeStorage(payload)
        fake_field = StubFieldFile(fake_storage, "videos/test.mp4")
        fake_video_obj = SimpleNamespace(
            active_raw_file=fake_field,
            processed_file=fake_field,
            storage_mode=view_module.VideoFile.StorageMode.APP_ENCRYPTED,
        )

        monkeypatches = pytest.MonkeyPatch()
        try:
            monkeypatches.setattr(
                view_module.VideoFile.objects, "get", lambda **kwargs: fake_video_obj
            )
            response = self.client.get(
                "/api/media/videos/123/stream/?type=processed",
                HTTP_RANGE="bytes=25-99",
            )
        finally:
            monkeypatches.undo()

        assert response.status_code == 206
        assert response["Content-Range"] == f"bytes 25-99/{len(payload)}"
        assert b"".join(response.streaming_content) == payload[25:100]

    def test_video_stream_does_not_emit_nginx_redirect_for_plaintext_path_outside_protected_root(
        self,
    ):
        from endoreg_db.views.video import video_stream as view_module

        tmp_file_path = None
        monkeypatches = pytest.MonkeyPatch()
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp.write(b"\x00\x00\x00\x18ftypmp42")
                tmp.flush()
                tmp_file_path = Path(tmp.name)

            fake_file_field = SimpleNamespace(
                name=tmp_file_path.name,
                path=str(tmp_file_path),
                size=tmp_file_path.stat().st_size,
            )
            fake_video_obj = SimpleNamespace(
                active_raw_file=fake_file_field,
                processed_file=fake_file_field,
                storage_mode=view_module.VideoFile.StorageMode.FS_ENCRYPTED_STREAMABLE,
            )

            monkeypatches.setenv("SERVE_WITH_NGINX", "true")
            monkeypatches.setattr(
                view_module.VideoFile.objects, "get", lambda **kwargs: fake_video_obj
            )

            response = self.client.get("/api/media/videos/123/stream/?type=raw")
        finally:
            monkeypatches.undo()
            if tmp_file_path and tmp_file_path.exists():
                tmp_file_path.unlink(missing_ok=True)

        assert response.status_code == 200
        assert "X-Accel-Redirect" not in response

    def test_video_stream_plaintext_legacy_mode_does_not_emit_nginx_redirect(self):
        from endoreg_db.views.video import video_stream as view_module

        storage_dir = STORAGE_DIR.resolve()
        storage_dir.mkdir(parents=True, exist_ok=True)
        tmp_file_path = None
        monkeypatches = pytest.MonkeyPatch()
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".mp4", dir=storage_dir, delete=False
            ) as tmp:
                tmp.write(b"\x00\x00\x00\x18ftypmp42")
                tmp.flush()
                tmp_file_path = Path(tmp.name)

            relative_name = tmp_file_path.relative_to(storage_dir).as_posix()
            fake_file_field = SimpleNamespace(
                name=relative_name,
                size=tmp_file_path.stat().st_size,
            )
            fake_video_obj = SimpleNamespace(
                active_raw_file=fake_file_field,
                processed_file=fake_file_field,
                storage_mode=view_module.VideoFile.StorageMode.APP_ENCRYPTED,
                streamable_relative_path="streamable_videos/raw/test.mp4",
                processed_streamable_relative_path="streamable_videos/processed/test.mp4",
            )

            monkeypatches.setenv("SERVE_WITH_NGINX", "true")
            monkeypatches.setattr(
                view_module.VideoFile.objects, "get", lambda **kwargs: fake_video_obj
            )

            response = self.client.get("/api/media/videos/123/stream/?type=raw")
        finally:
            monkeypatches.undo()
            if tmp_file_path and tmp_file_path.exists():
                tmp_file_path.unlink(missing_ok=True)

        assert response.status_code == 200
        assert "X-Accel-Redirect" not in response

    def test_video_stream_returns_conflict_when_streamable_artifact_is_not_ready(self):
        from endoreg_db.views.video import video_stream as view_module

        payload = b"\x00\x00\x00\x18ftypmp42"
        fake_storage = FakeStorage(payload)
        fake_field = StubFieldFile(fake_storage, "videos/test.mp4")
        fake_video_obj = SimpleNamespace(
            active_raw_file=fake_field,
            processed_file=fake_field,
            storage_mode=view_module.VideoFile.StorageMode.FS_ENCRYPTED_STREAMABLE,
            streamable_relative_path="streamable_videos/raw/missing.mp4",
            processed_streamable_relative_path="streamable_videos/processed/missing.mp4",
        )

        monkeypatches = pytest.MonkeyPatch()
        try:
            monkeypatches.setattr(
                view_module.VideoFile.objects, "get", lambda **kwargs: fake_video_obj
            )
            response = self.client.get("/api/media/videos/123/stream/?type=raw")
        finally:
            monkeypatches.undo()

        assert response.status_code == 409
        assert response["X-Stream-State"] == "missing_streamable_artifact"

    def test_video_stream_uses_configured_protected_media_root_for_streamable_paths(
        self,
    ):
        from endoreg_db.views.video import video_stream as view_module

        monkeypatches = pytest.MonkeyPatch()
        fake_file_field = SimpleNamespace(
            name="upload_jobs/api/test.mp4",
            size=12,
        )
        fake_video_obj = SimpleNamespace(
            active_raw_file=fake_file_field,
            processed_file=fake_file_field,
            storage_mode=view_module.VideoFile.StorageMode.FS_ENCRYPTED_STREAMABLE,
            streamable_relative_path="streamable_videos/raw/test.mp4",
            processed_streamable_relative_path="streamable_videos/processed/test.mp4",
        )

        storage_dir = STORAGE_DIR.resolve()
        protected_media_root = storage_dir.parent / "protected_media_mount"
        streamable_path = (
            protected_media_root / "streamable_videos" / "raw" / "test.mp4"
        )
        streamable_path.parent.mkdir(parents=True, exist_ok=True)
        streamable_path.write_bytes(b"\x00\x00\x00\x18ftypmp42")

        try:
            monkeypatches.setenv("SERVE_WITH_NGINX", "true")
            monkeypatches.setenv("PROTECTED_MEDIA_ROOT", str(protected_media_root))
            monkeypatches.setattr(
                view_module, "NGINX_PROTECTED_URL", "/protected_media/"
            )
            monkeypatches.setattr(
                view_module.VideoFile.objects, "get", lambda **kwargs: fake_video_obj
            )

            response = self.client.get("/api/media/videos/123/stream/?type=raw")
        finally:
            monkeypatches.undo()
            if streamable_path.exists():
                streamable_path.unlink(missing_ok=True)

        assert response.status_code == 200
        assert (
            response["X-Accel-Redirect"]
            == "/protected_media/streamable_videos/raw/test.mp4"
        )

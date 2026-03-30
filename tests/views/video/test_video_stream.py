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

        assert response.status_code == 200
        assert "X-Accel-Redirect" in response
        assert response["X-Accel-Redirect"].startswith("/protected_media/")
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

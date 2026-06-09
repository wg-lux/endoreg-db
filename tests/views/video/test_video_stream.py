from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import IO, Any

import pytest
from django.test import TestCase

from endoreg_db.utils.encryption.encrypted import MAGIC as LX_ENCRYPTED_MAGIC
from endoreg_db.utils.filesystem.paths import protected_media_root


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


class LocalStubFieldFile:
    def __init__(self, name: str):
        self.name = name
        self.file: IO[Any]

    @property
    def path(self) -> str:
        return str((protected_media_root() / self.name).resolve())

    @property
    def size(self) -> int:
        return Path(self.path).stat().st_size

    def open(self, mode: str = "rb") -> None:
        self.file = open(self.path, mode)

    def close(self) -> None:
        if self.file is not None:
            self.file.close()

    def chunks(self, chunk_size: int = 64 * 1024):
        with open(self.path, "rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                yield chunk


def attach_video_stream_methods(fake_video_obj, view_module) -> None:
    def streamable_path_is_safe_plaintext(path: Path) -> bool:
        if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
            return False
        with path.open("rb") as handle:
            return handle.read(len(LX_ENCRYPTED_MAGIC)) != LX_ENCRYPTED_MAGIC

    def get_stream_relative_path(file_type: str) -> str | None:
        attr_name = (
            "processed_streamable_relative_path"
            if file_type == "processed"
            else "raw_streamable_relative_path"
        )
        return getattr(fake_video_obj, attr_name, None)

    def can_offload_stream_with_nginx(file_type: str) -> bool:
        return (
            getattr(fake_video_obj, "storage_mode", None)
            == view_module.VideoFile.StorageMode.FS_ENCRYPTED_STREAMABLE
            and get_stream_relative_path(file_type) is not None
        )

    def resolve_video_stream_source(
        file_type: str,
        *,
        materialize_if_missing: bool = False,  # noqa: ARG001
    ):
        if file_type == "processed":
            field_file = getattr(fake_video_obj, "processed_file", None)
            fallback = getattr(fake_video_obj, "get_processed_file_path", None)
        else:
            field_file = getattr(fake_video_obj, "active_raw_file", None)
            fallback = getattr(fake_video_obj, "get_raw_file_path", None)
        if field_file is None or not getattr(field_file, "name", None):
            raise FileNotFoundError(f"No {file_type} file")

        stream_relative_path = get_stream_relative_path(file_type)
        if stream_relative_path:
            stream_path = protected_media_root() / stream_relative_path
            if streamable_path_is_safe_plaintext(stream_path):
                return field_file, stream_path

        field_path = getattr(field_file, "path", None)
        if field_path and Path(field_path).exists():
            return field_file, Path(field_path)

        if callable(fallback):
            fallback_path = fallback()
            if fallback_path is not None and Path(fallback_path).exists():
                return field_file, Path(fallback_path)

        if getattr(field_file, "storage", None) is not None:
            return field_file, None
        raise FileNotFoundError(f"{file_type.title()} video file is not available")

    fake_video_obj.get_stream_relative_path = get_stream_relative_path
    fake_video_obj.can_offload_stream_with_nginx = can_offload_stream_with_nginx
    fake_video_obj.resolve_video_stream_source = resolve_video_stream_source


class VideoStreamViewTests(TestCase):
    def test_video_stream_nginx_headers(self):
        from endoreg_db.views.video import video_stream as view_module

        storage_dir = protected_media_root().resolve()
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
                raw_streamable_relative_path="streamable_videos/raw/test.mp4",
                processed_streamable_relative_path="streamable_videos/processed/test.mp4",
            )
            fake_lease = SimpleNamespace(token="nginx-lease-token")
            lease_calls = {}
            attach_video_stream_methods(fake_video_obj, view_module)

            def fake_create_video_stream_lease(video, *, file_type):
                lease_calls["video"] = video
                lease_calls["file_type"] = file_type
                return fake_lease

            monkeypatches.setenv("SERVE_WITH_NGINX", "true")
            monkeypatches.setenv(
                "DJANGO_CORS_ALLOWED_ORIGINS",
                "http://frontend.test",
            )
            monkeypatches.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
            monkeypatches.setattr(
                view_module.VideoStreamView,
                "_get_video_or_404",
                staticmethod(lambda pk: fake_video_obj),
            )
            monkeypatches.setattr(
                view_module,
                "create_video_stream_lease",
                fake_create_video_stream_lease,
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
        assert response["X-Media-Operation-Lease"] == "nginx-lease-token"
        assert lease_calls == {"video": fake_video_obj, "file_type": "raw"}

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
        attach_video_stream_methods(fake_video_obj, view_module)

        monkeypatches = pytest.MonkeyPatch()
        try:
            monkeypatches.setattr(
                view_module.VideoStreamView,
                "_get_video_or_404",
                staticmethod(lambda pk: fake_video_obj),
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

    def test_video_stream_wraps_django_stream_with_media_lease(self):
        from endoreg_db.views.video import video_stream as view_module

        payload = b"\x00\x00\x00\x18ftypmp42lease"
        fake_storage = FakeStorage(payload)
        fake_field = StubFieldFile(fake_storage, "videos/test.mp4")
        fake_video_obj = SimpleNamespace(
            active_raw_file=fake_field,
            processed_file=fake_field,
            storage_mode=view_module.VideoFile.StorageMode.APP_ENCRYPTED,
        )
        fake_lease = SimpleNamespace(token="lease-token")
        calls = {}
        attach_video_stream_methods(fake_video_obj, view_module)

        def fake_create_video_stream_lease(video, *, file_type):
            calls["video"] = video
            calls["file_type"] = file_type
            return fake_lease

        def fake_wrap_iterator_with_media_lease(chunks, lease):
            calls["lease"] = lease
            yield from chunks

        monkeypatches = pytest.MonkeyPatch()
        try:
            monkeypatches.setattr(
                view_module.VideoStreamView,
                "_get_video_or_404",
                staticmethod(lambda pk: fake_video_obj),
            )
            monkeypatches.setattr(
                view_module,
                "create_video_stream_lease",
                fake_create_video_stream_lease,
            )
            monkeypatches.setattr(
                view_module,
                "wrap_iterator_with_media_lease",
                fake_wrap_iterator_with_media_lease,
            )

            response = self.client.get("/api/media/videos/123/stream/?type=processed")
            body = b"".join(response.streaming_content)
        finally:
            monkeypatches.undo()

        assert response.status_code == 200
        assert response["X-Media-Operation-Lease"] == "lease-token"
        assert body == payload
        assert calls == {
            "video": fake_video_obj,
            "file_type": "processed",
            "lease": fake_lease,
        }

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
            attach_video_stream_methods(fake_video_obj, view_module)

            monkeypatches.setenv("SERVE_WITH_NGINX", "true")
            monkeypatches.setattr(
                view_module.VideoStreamView,
                "_get_video_or_404",
                staticmethod(lambda pk: fake_video_obj),
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

        storage_dir = protected_media_root().resolve()
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
                raw_streamable_relative_path="streamable_videos/raw/test.mp4",
                processed_streamable_relative_path="streamable_videos/processed/test.mp4",
            )
            attach_video_stream_methods(fake_video_obj, view_module)

            monkeypatches.setenv("SERVE_WITH_NGINX", "true")
            monkeypatches.setattr(
                view_module.VideoStreamView,
                "_get_video_or_404",
                staticmethod(lambda pk: fake_video_obj),
            )

            response = self.client.get("/api/media/videos/123/stream/?type=raw")
        finally:
            monkeypatches.undo()
            if tmp_file_path and tmp_file_path.exists():
                tmp_file_path.unlink(missing_ok=True)

        assert response.status_code == 200
        assert "X-Accel-Redirect" not in response

    def test_video_stream_falls_back_when_streamable_artifact_is_not_ready(self):
        from endoreg_db.views.video import video_stream as view_module

        payload = b"\x00\x00\x00\x18ftypmp42"
        fake_storage = FakeStorage(payload)
        fake_field = StubFieldFile(fake_storage, "videos/test.mp4")
        fake_video_obj = SimpleNamespace(
            active_raw_file=fake_field,
            processed_file=fake_field,
            storage_mode=view_module.VideoFile.StorageMode.FS_ENCRYPTED_STREAMABLE,
            raw_streamable_relative_path="streamable_videos/raw/missing.mp4",
            processed_streamable_relative_path="streamable_videos/processed/missing.mp4",
        )
        attach_video_stream_methods(fake_video_obj, view_module)

        monkeypatches = pytest.MonkeyPatch()
        try:
            monkeypatches.setattr(
                view_module.VideoStreamView,
                "_get_video_or_404",
                staticmethod(lambda pk: fake_video_obj),
            )
            response = self.client.get("/api/media/videos/123/stream/?type=processed")
            body = b"".join(response.streaming_content)
        finally:
            monkeypatches.undo()

        assert response.status_code == 200
        assert response["X-Stream-State"] == "missing_streamable_artifact"
        assert body == payload

    def test_video_stream_rejects_raw_django_fallback_by_default(self):
        from endoreg_db.views.video import video_stream as view_module

        payload = b"\x00\x00\x00\x18ftypmp42"
        fake_storage = FakeStorage(payload)
        fake_field = StubFieldFile(fake_storage, "videos/test.mp4")
        fake_video_obj = SimpleNamespace(
            active_raw_file=fake_field,
            processed_file=fake_field,
            storage_mode=view_module.VideoFile.StorageMode.APP_ENCRYPTED,
        )
        attach_video_stream_methods(fake_video_obj, view_module)

        monkeypatches = pytest.MonkeyPatch()
        try:
            monkeypatches.delenv("ENDOREG_ALLOW_RAW_DJANGO_STREAMING", raising=False)
            monkeypatches.setattr(
                view_module.VideoStreamView,
                "_get_video_or_404",
                staticmethod(lambda pk: fake_video_obj),
            )
            response = self.client.get("/api/media/videos/123/stream/?type=raw")
        finally:
            monkeypatches.undo()

        assert response.status_code == 409
        assert response["X-Stream-State"] == "raw_django_streaming_disabled"

    def test_video_stream_allows_raw_django_fallback_when_explicitly_enabled(self):
        from endoreg_db.views.video import video_stream as view_module

        payload = b"\x00\x00\x00\x18ftypmp42"
        fake_storage = FakeStorage(payload)
        fake_field = StubFieldFile(fake_storage, "videos/test.mp4")
        fake_video_obj = SimpleNamespace(
            active_raw_file=fake_field,
            processed_file=fake_field,
            storage_mode=view_module.VideoFile.StorageMode.APP_ENCRYPTED,
        )
        attach_video_stream_methods(fake_video_obj, view_module)

        monkeypatches = pytest.MonkeyPatch()
        try:
            monkeypatches.setenv("ENDOREG_ALLOW_RAW_DJANGO_STREAMING", "true")
            monkeypatches.setattr(
                view_module.VideoStreamView,
                "_get_video_or_404",
                staticmethod(lambda pk: fake_video_obj),
            )
            response = self.client.get("/api/media/videos/123/stream/?type=raw")
            body = b"".join(response.streaming_content)
        finally:
            monkeypatches.undo()

        assert response.status_code == 200
        assert body == payload

    def test_video_stream_falls_back_when_streamable_artifact_is_encrypted(self):
        from endoreg_db.views.video import video_stream as view_module

        payload = b"\x00\x00\x00\x18ftypmp42plaintext-canonical"
        fake_storage = FakeStorage(payload)
        fake_field = StubFieldFile(fake_storage, "videos/test.mp4")
        fake_video_obj = SimpleNamespace(
            raw_file=fake_field,
            active_raw_file=fake_field,
            processed_file=fake_field,
            storage_mode=view_module.VideoFile.StorageMode.FS_ENCRYPTED_STREAMABLE,
            raw_streamable_relative_path="streamable_videos/raw/encrypted.mp4",
            processed_streamable_relative_path="streamable_videos/processed/encrypted.mp4",
        )
        attach_video_stream_methods(fake_video_obj, view_module)

        streamable_path = (
            protected_media_root() / fake_video_obj.processed_streamable_relative_path
        )
        streamable_path.parent.mkdir(parents=True, exist_ok=True)
        streamable_path.write_bytes(LX_ENCRYPTED_MAGIC + b"ciphertext")

        monkeypatches = pytest.MonkeyPatch()
        try:
            monkeypatches.setenv("SERVE_WITH_NGINX", "true")
            monkeypatches.setattr(
                view_module.VideoStreamView,
                "_get_video_or_404",
                staticmethod(lambda pk: fake_video_obj),
            )
            response = self.client.get("/api/media/videos/123/stream/?type=processed")
            body = b"".join(response.streaming_content)
        finally:
            monkeypatches.undo()
            streamable_path.unlink(missing_ok=True)

        assert response.status_code == 200
        assert "X-Accel-Redirect" not in response
        assert response["X-Stream-State"] == "encrypted_streamable_artifact"
        assert not body.startswith(LX_ENCRYPTED_MAGIC)
        assert body == payload

    def test_video_stream_repairs_encrypted_streamable_artifact_before_nginx_handoff(
        self,
    ):
        from endoreg_db.views.video import video_stream as view_module

        payload = b"\x00\x00\x00\x18ftypmp42"
        fake_storage = FakeStorage(payload)
        fake_field = StubFieldFile(fake_storage, "videos/test.mp4")
        fake_video_obj = SimpleNamespace(
            raw_file=fake_field,
            active_raw_file=fake_field,
            processed_file=fake_field,
            storage_mode=view_module.VideoFile.StorageMode.FS_ENCRYPTED_STREAMABLE,
            raw_streamable_relative_path="streamable_videos/raw/repaired.mp4",
            processed_streamable_relative_path="streamable_videos/processed/repaired.mp4",
        )
        attach_video_stream_methods(fake_video_obj, view_module)

        streamable_path = (
            protected_media_root() / fake_video_obj.processed_streamable_relative_path
        )
        streamable_path.parent.mkdir(parents=True, exist_ok=True)
        streamable_path.write_bytes(LX_ENCRYPTED_MAGIC + b"ciphertext")

        def fake_sync(video, *, include_raw, include_processed, save):  # noqa: ARG001
            assert include_raw is False
            assert include_processed is True
            assert save is True
            streamable_path.write_bytes(payload)
            return ["processed_streamable_relative_path"]

        monkeypatches = pytest.MonkeyPatch()
        try:
            monkeypatches.setenv("SERVE_WITH_NGINX", "true")
            monkeypatches.setattr(
                view_module,
                "sync_video_streamable_artifacts",
                fake_sync,
            )
            monkeypatches.setattr(
                view_module.VideoStreamView,
                "_get_video_or_404",
                staticmethod(lambda pk: fake_video_obj),
            )
            response = self.client.get("/api/media/videos/123/stream/?type=processed")
        finally:
            monkeypatches.undo()
            streamable_path.unlink(missing_ok=True)

        assert response.status_code == 200
        assert (
            response["X-Accel-Redirect"]
            == "/protected_media/streamable_videos/processed/repaired.mp4"
        )
        assert "X-Stream-State" not in response

    def test_video_stream_recovers_raw_path_when_field_name_is_stale(self):
        from endoreg_db.views.video import video_stream as view_module

        payload = b"\x00\x00\x00\x18ftypmp42raw-fallback"
        storage_dir = protected_media_root().resolve()
        fallback_path = storage_dir / "sensitive_videos" / "fallback-raw.mp4"
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        fallback_path.write_bytes(payload)

        fake_raw_field = LocalStubFieldFile("sensitive_videos/missing.mp4")
        fake_video_obj = SimpleNamespace(
            raw_file=fake_raw_field,
            active_raw_file=fake_raw_field,
            processed_file=fake_raw_field,
            storage_mode=view_module.VideoFile.StorageMode.APP_ENCRYPTED,
            get_raw_file_path=lambda: fallback_path,
            get_processed_file_path=lambda: fallback_path,
        )
        attach_video_stream_methods(fake_video_obj, view_module)

        monkeypatches = pytest.MonkeyPatch()
        try:
            monkeypatches.setattr(
                view_module.VideoStreamView,
                "_get_video_or_404",
                staticmethod(lambda pk: fake_video_obj),
            )
            response = self.client.get("/api/media/videos/123/stream/?type=raw")
            body = b"".join(response.streaming_content)
        finally:
            monkeypatches.undo()
            fallback_path.unlink(missing_ok=True)

        assert response.status_code == 200
        assert body == payload

    def test_video_stream_recovers_processed_path_when_field_name_is_stale(self):
        from endoreg_db.views.video import video_stream as view_module

        payload = b"\x00\x00\x00\x18ftypmp42processed-fallback"
        storage_dir = protected_media_root().resolve()
        fallback_path = (
            storage_dir / "processed_videos_final" / "fallback-processed.mp4"
        )
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        fallback_path.write_bytes(payload)

        fake_processed_field = LocalStubFieldFile("processed_videos_final/missing.mp4")
        fake_raw_field = LocalStubFieldFile("sensitive_videos/unused.mp4")
        fake_video_obj = SimpleNamespace(
            raw_file=fake_raw_field,
            active_raw_file=fake_raw_field,
            processed_file=fake_processed_field,
            storage_mode=view_module.VideoFile.StorageMode.APP_ENCRYPTED,
            get_raw_file_path=lambda: None,
            get_processed_file_path=lambda: fallback_path,
        )
        attach_video_stream_methods(fake_video_obj, view_module)

        monkeypatches = pytest.MonkeyPatch()
        try:
            monkeypatches.setattr(
                view_module.VideoStreamView,
                "_get_video_or_404",
                staticmethod(lambda pk: fake_video_obj),
            )
            response = self.client.get("/api/media/videos/123/stream/?type=processed")
            body = b"".join(response.streaming_content)
        finally:
            monkeypatches.undo()
            fallback_path.unlink(missing_ok=True)

        assert response.status_code == 200
        assert body == payload

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
            raw_streamable_relative_path="streamable_videos/raw/test.mp4",
            processed_streamable_relative_path="streamable_videos/processed/test.mp4",
        )
        attach_video_stream_methods(fake_video_obj, view_module)

        storage_dir = protected_media_root().resolve()
        protected_media_root_path = storage_dir.parent / "protected_media_mount"
        streamable_path = (
            protected_media_root_path / "streamable_videos" / "raw" / "test.mp4"
        )
        streamable_path.parent.mkdir(parents=True, exist_ok=True)
        streamable_path.write_bytes(b"\x00\x00\x00\x18ftypmp42")

        try:
            monkeypatches.setenv("SERVE_WITH_NGINX", "true")
            monkeypatches.setenv("PROTECTED_MEDIA_ROOT", str(protected_media_root_path))
            monkeypatches.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
            monkeypatches.setattr(
                view_module.VideoStreamView,
                "_get_video_or_404",
                staticmethod(lambda pk: fake_video_obj),
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

    def test_video_stream_invalid_range_returns_416_with_content_range(self):
        from endoreg_db.views.video import video_stream as view_module

        payload = b"\x00\x00\x00\x18ftypmp42"
        fake_storage = FakeStorage(payload)
        fake_field = StubFieldFile(fake_storage, "videos/test.mp4")
        fake_video_obj = SimpleNamespace(
            active_raw_file=fake_field,
            processed_file=fake_field,
            storage_mode=view_module.VideoFile.StorageMode.APP_ENCRYPTED,
        )
        attach_video_stream_methods(fake_video_obj, view_module)

        monkeypatches = pytest.MonkeyPatch()
        try:
            monkeypatches.setattr(
                view_module.VideoStreamView,
                "_get_video_or_404",
                staticmethod(lambda pk: fake_video_obj),
            )
            response = self.client.get(
                "/api/media/videos/123/stream/?type=processed",
                HTTP_RANGE="bytes=999-1000",
            )
        finally:
            monkeypatches.undo()

        assert response.status_code == 416
        assert response["Content-Range"] == f"bytes */{len(payload)}"
        assert response["Accept-Ranges"] == "bytes"

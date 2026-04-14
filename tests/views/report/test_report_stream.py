from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import IO

import pytest
from django.test import TestCase

from endoreg_db.utils.paths import ANONYM_REPORT_DIR, STORAGE_DIR


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
        self.file: IO[bytes] | None = None

    @property
    def path(self) -> str:
        return str((STORAGE_DIR / self.name).resolve())

    @property
    def size(self) -> int:
        return Path(self.path).stat().st_size

    def open(self, mode: str = "rb") -> None:
        self.file = open(self.path, mode)

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None

    def chunks(self, chunk_size: int = 64 * 1024):
        with open(self.path, "rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                yield chunk


class ReportStreamViewTests(TestCase):
    def test_pdf_stream_download_nginx_headers(self):
        from endoreg_db.views.report import report_stream as view_module

        storage_dir = STORAGE_DIR.resolve()
        storage_dir.mkdir(parents=True, exist_ok=True)
        tmp_file_path = None
        monkeypatches = pytest.MonkeyPatch()
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".pdf", dir=storage_dir, delete=False
            ) as tmp:
                tmp.write(b"%PDF-1.4\n%test\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
                tmp.flush()
                tmp_file_path = Path(tmp.name)

            relative_name = tmp_file_path.relative_to(storage_dir).as_posix()
            fake_file_field = SimpleNamespace(
                name=relative_name,
                size=tmp_file_path.stat().st_size,
            )
            fake_pdf_obj = SimpleNamespace(file=fake_file_field, processed_file=None)

            monkeypatches.setenv("SERVE_WITH_NGINX", "true")
            monkeypatches.setenv("FRONTEND_ORIGIN", "http://frontend.test")
            monkeypatches.setattr(
                view_module, "NGINX_PROTECTED_URL", "/protected_media/"
            )
            monkeypatches.setattr(
                view_module.RawPdfFile.objects, "get", lambda **kwargs: fake_pdf_obj
            )

            resp = self.client.get("/api/media/pdfs/123/stream/?type=raw&download=1")
        finally:
            monkeypatches.undo()
            if tmp_file_path and tmp_file_path.exists():
                tmp_file_path.unlink(missing_ok=True)

        assert resp.status_code == 200
        assert "X-Accel-Redirect" in resp
        assert resp["X-Accel-Redirect"].startswith("/protected_media/")
        assert resp["Content-Disposition"].startswith("attachment;")
        assert "filename=" in resp["Content-Disposition"]
        assert resp["X-Accel-Buffering"] == "no"

    def test_pdf_stream_range_uses_storage_api_hooks(self):
        from endoreg_db.views.report import report_stream as view_module

        payload = (b"%PDF-1.4\n" * 1024) + b"%%EOF\n"
        fake_storage = FakeStorage(payload)
        fake_field = StubFieldFile(fake_storage, "reports/test.pdf")
        fake_pdf_obj = SimpleNamespace(file=fake_field, processed_file=fake_field)

        monkeypatches = pytest.MonkeyPatch()
        try:
            monkeypatches.setattr(
                view_module.RawPdfFile.objects, "get", lambda **kwargs: fake_pdf_obj
            )
            response = self.client.get(
                "/api/media/pdfs/123/stream/?type=processed",
                HTTP_RANGE="bytes=10-49",
            )
        finally:
            monkeypatches.undo()

        assert response.status_code == 206
        assert response["Content-Range"] == f"bytes 10-49/{len(payload)}"
        assert b"".join(response.streaming_content) == payload[10:50]

    def test_pdf_stream_encrypted_storage_falls_back_to_django_streaming(self):
        from endoreg_db.views.report import report_stream as view_module

        payload = (b"%PDF-1.4\n" * 32) + b"%%EOF\n"
        fake_storage = FakeStorage(payload)
        fake_field = StubFieldFile(fake_storage, "reports/encrypted.pdf")
        fake_pdf_obj = SimpleNamespace(file=fake_field, processed_file=fake_field)

        monkeypatches = pytest.MonkeyPatch()
        try:
            monkeypatches.setenv("SERVE_WITH_NGINX", "true")
            monkeypatches.setattr(
                view_module.RawPdfFile.objects, "get", lambda **kwargs: fake_pdf_obj
            )

            response = self.client.get("/api/media/pdfs/123/stream/?type=raw")
        finally:
            monkeypatches.undo()

        assert response.status_code == 200
        assert "X-Accel-Redirect" not in response
        assert b"".join(response.streaming_content) == payload

    def test_pdf_stream_recovers_raw_path_from_hash_lookup_when_field_name_is_stale(
        self,
    ):
        from endoreg_db.views.report import report_stream as view_module

        payload = b"%PDF-1.4\nraw-fallback\n%%EOF\n"
        storage_dir = STORAGE_DIR.resolve()
        fallback_path = storage_dir / "sensitive_reports" / "fallback-raw.pdf"
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        fallback_path.write_bytes(payload)

        fake_file_field = LocalStubFieldFile("sensitive_reports/missing.pdf")
        fake_pdf_obj = SimpleNamespace(
            file=fake_file_field,
            processed_file=None,
            get_raw_file_path=lambda: fallback_path,
        )

        monkeypatches = pytest.MonkeyPatch()
        try:
            monkeypatches.setattr(
                view_module.RawPdfFile.objects, "get", lambda **kwargs: fake_pdf_obj
            )
            response = self.client.get("/api/media/pdfs/123/stream/?type=raw")
        finally:
            monkeypatches.undo()
            fallback_path.unlink(missing_ok=True)

        assert response.status_code == 200
        assert b"".join(response.streaming_content) == payload

    def test_pdf_stream_recovers_processed_path_from_hash_lookup_when_field_name_is_stale(
        self,
    ):
        from endoreg_db.views.report import report_stream as view_module

        payload = b"%PDF-1.4\nprocessed-fallback\n%%EOF\n"
        fallback_path = ANONYM_REPORT_DIR / "processed-hash.pdf"
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        fallback_path.write_bytes(payload)

        fake_processed_field = LocalStubFieldFile("processed_reports_final/missing.pdf")
        fake_pdf_obj = SimpleNamespace(
            pdf_hash="processed-hash",
            file=None,
            processed_file=fake_processed_field,
            get_raw_file_path=lambda: None,
        )

        monkeypatches = pytest.MonkeyPatch()
        try:
            monkeypatches.setattr(
                view_module.RawPdfFile.objects, "get", lambda **kwargs: fake_pdf_obj
            )
            response = self.client.get("/api/media/pdfs/123/stream/?type=processed")
        finally:
            monkeypatches.undo()
            fallback_path.unlink(missing_ok=True)

        assert response.status_code == 200
        assert b"".join(response.streaming_content) == payload

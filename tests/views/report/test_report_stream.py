from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import IO

import pytest
from django.test import TestCase

from endoreg_db.utils.filesystem.paths import ANONYM_REPORT_DIR, protected_media_root
from endoreg_db.utils.encryption.encrypted import MAGIC as LX_ENCRYPTED_MAGIC


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
        return str((protected_media_root() / self.name).resolve())

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

        storage_dir = protected_media_root().resolve()
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
            fake_file_field = LocalStubFieldFile(relative_name)
            fake_pdf_obj = SimpleNamespace(file=fake_file_field, processed_file=None)

            monkeypatches.setenv("SERVE_WITH_NGINX", "true")
            monkeypatches.setenv(
                "DJANGO_CORS_ALLOWED_ORIGINS",
                "http://frontend.test",
            )
            monkeypatches.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
            monkeypatches.setattr(
                view_module,
                "_resolve_local_path_for_nginx",
                lambda field_file: tmp_file_path,
            )

            resp = view_module._serve_with_nginx(
                fake_pdf_obj.file,
                "application/pdf",
                disposition="attachment",
                frontend_origin="http://frontend.test",
            )
        finally:
            monkeypatches.undo()
            if tmp_file_path and tmp_file_path.exists():
                tmp_file_path.unlink(missing_ok=True)

        assert resp is not None
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

        response = view_module._build_eager_content_response(
            field_file=fake_field,
            content_type="application/pdf",
            file_size=len(payload),
            range_header="bytes=10-49",
            disposition="inline",
            filename="test.pdf",
        )

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
            nginx_response = view_module._serve_with_nginx(
                fake_pdf_obj.file,
                "application/pdf",
                disposition="inline",
                frontend_origin=None,
            )
            response = view_module._build_eager_content_response(
                field_file=fake_pdf_obj.file,
                content_type="application/pdf",
                file_size=len(payload),
                range_header=None,
                disposition="inline",
                filename="encrypted.pdf",
            )
        finally:
            monkeypatches.undo()

        assert nginx_response is None
        assert response.status_code == 200
        assert "X-Accel-Redirect" not in response
        assert b"".join(response.streaming_content) == payload

    def test_pdf_stream_encrypted_storage_does_not_offload_by_storage_name_only(self):
        from endoreg_db.views.report import report_stream as view_module

        payload = (b"%PDF-1.4\n" * 16) + b"%%EOF\n"
        fake_storage = FakeStorage(payload)
        relative_name = "reports/encrypted-present-on-disk.pdf"
        fake_field = StubFieldFile(fake_storage, relative_name)
        fake_pdf_obj = SimpleNamespace(file=fake_field, processed_file=fake_field)

        storage_dir = protected_media_root().resolve()
        disk_path = storage_dir / relative_name
        disk_path.parent.mkdir(parents=True, exist_ok=True)
        disk_path.write_bytes(b"ciphertext-placeholder")

        monkeypatches = pytest.MonkeyPatch()
        try:
            monkeypatches.setenv("SERVE_WITH_NGINX", "true")
            monkeypatches.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
            nginx_response = view_module._serve_with_nginx(
                fake_pdf_obj.file,
                "application/pdf",
                disposition="inline",
                frontend_origin=None,
            )
            response = view_module._build_eager_content_response(
                field_file=fake_pdf_obj.file,
                content_type="application/pdf",
                file_size=len(payload),
                range_header=None,
                disposition="inline",
                filename=relative_name,
            )
        finally:
            monkeypatches.undo()
            disk_path.unlink(missing_ok=True)

        assert nginx_response is None
        assert response.status_code == 200
        assert "X-Accel-Redirect" not in response
        assert b"".join(response.streaming_content) == payload

    def test_pdf_stream_local_lxenc_file_without_decrypting_storage_is_rejected(self):
        from endoreg_db.views.report import report_stream as view_module

        storage_dir = protected_media_root().resolve()
        relative_name = "reports/local-ciphertext.pdf"
        disk_path = storage_dir / relative_name
        disk_path.parent.mkdir(parents=True, exist_ok=True)
        disk_path.write_bytes(LX_ENCRYPTED_MAGIC + b"ciphertext")
        fake_field = LocalStubFieldFile(relative_name)

        try:
            assert view_module._resolve_local_path_for_nginx(fake_field) is None
            assert view_module.field_file_is_local_encrypted_without_reader(fake_field)
        finally:
            disk_path.unlink(missing_ok=True)

    def test_pdf_stream_recovers_raw_path_from_hash_lookup_when_field_name_is_stale(
        self,
    ):
        from endoreg_db.views.report import report_stream as view_module

        payload = b"%PDF-1.4\nraw-fallback\n%%EOF\n"
        storage_dir = protected_media_root().resolve()
        fallback_path = storage_dir / "sensitive_reports" / "raw-fallback-hash.pdf"
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        fallback_path.write_bytes(payload)

        fake_file_field = LocalStubFieldFile("sensitive_reports/missing.pdf")
        fake_pdf_obj = SimpleNamespace(
            pdf_hash="raw-fallback-hash",
            file=fake_file_field,
            processed_file=None,
        )

        try:
            recovered = view_module._recover_missing_report_field_path(
                fake_pdf_obj,
                "raw",
            )
            response = view_module._build_eager_content_response(
                field_file=recovered,
                content_type="application/pdf",
                file_size=len(payload),
                range_header=None,
                disposition="inline",
                filename="fallback-raw.pdf",
            )
            body = b"".join(response.streaming_content)
        finally:
            fallback_path.unlink(missing_ok=True)

        assert recovered is fake_pdf_obj.file
        assert response.status_code == 200
        assert body == payload

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
        )

        try:
            recovered = view_module._recover_missing_report_field_path(
                fake_pdf_obj,
                "processed",
            )
            response = view_module._build_eager_content_response(
                field_file=recovered,
                content_type="application/pdf",
                file_size=len(payload),
                range_header=None,
                disposition="inline",
                filename="processed-hash.pdf",
            )
        finally:
            fallback_path.unlink(missing_ok=True)

        assert recovered is fake_pdf_obj.processed_file
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == payload

    def test_pdf_stream_invalid_range_returns_416_with_content_range(self):
        from django.http import HttpResponse
        from endoreg_db.views.report import report_stream as view_module

        payload = (b"%PDF-1.4\n" * 4) + b"%%EOF\n"

        with pytest.raises(ValueError):
            view_module.parse_byte_range("bytes=500-600", len(payload))
        response = HttpResponse(status=416, content_type="application/pdf")
        response["Content-Range"] = f"bytes */{len(payload)}"
        response["Accept-Ranges"] = "bytes"

        assert response.status_code == 416
        assert response["Content-Range"] == f"bytes */{len(payload)}"
        assert response["Accept-Ranges"] == "bytes"

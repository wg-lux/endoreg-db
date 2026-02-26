from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.test import TestCase

from endoreg_db.utils.paths import STORAGE_DIR


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
            fake_file_field = SimpleNamespace(name=relative_name)
            fake_pdf_obj = SimpleNamespace(file=fake_file_field, processed_file=None)
            fake_qs = SimpleNamespace(first=lambda: fake_pdf_obj)

            monkeypatches.setenv("SERVE_WITH_NGINX", "true")
            monkeypatches.setenv("FRONTEND_ORIGIN", "http://frontend.test")
            monkeypatches.setattr(
                view_module, "NGINX_PROTECTED_URL", "/protected_media/"
            )
            monkeypatches.setattr(
                view_module.RawPdfFile.objects, "filter", lambda **kwargs: fake_qs
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

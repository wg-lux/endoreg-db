from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage.sensitive_meta_storage import (
    sensitive_meta_storage,
)
from endoreg_db.import_files.processing.report_processing.report_anonymization import (
    ReportAnonymizer,
)
from endoreg_db.models import RawPdfFile
from endoreg_db.services.report_import import ReportImportService
from lx_anonymizer.sensitive_meta_interface import SensitiveMeta as LxSM
from tests.helpers.default_objects import DEFAULT_CENTER_NAME

MINIMAL_PDF_BYTES = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT
/F1 12 Tf
100 700 Td
(Sample PDF) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f
0000000010 00000 n
0000000060 00000 n
0000000110 00000 n
0000000210 00000 n
trailer
<< /Size 5 /Root 1 0 R >>
startxref
310
%%EOF
"""


class PdfMediaTextVisibilityTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from endoreg_db.helpers.data_load_orchestrator import load_base_db_data

        load_base_db_data()

    def test_txt_import_persists_text_and_is_frontend_readable(self):
        txt_content = "patient report from txt\nline two"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
            txt_path = Path(tmp.name)
        txt_path.write_text(txt_content, encoding="utf-8")

        try:
            report_obj = ReportImportService().import_and_anonymize(
                file_path=txt_path,
                center_name=DEFAULT_CENTER_NAME,
                retry=False,
            )
            assert isinstance(report_obj, RawPdfFile)
            report_obj.refresh_from_db()

            self.assertEqual(report_obj.text, txt_content)
            self.assertEqual(report_obj.anonymized_text, txt_content)

            response = self.client.get(f"/api/media/pdfs/{report_obj.pk}/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["anonymized_text"], txt_content)
            self.assertTrue(response.data["has_anonymized_text"])
        finally:
            txt_path.unlink(missing_ok=True)

    def test_pdf_import_persists_text_and_is_frontend_readable(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            pdf_path = Path(tmp.name)
        pdf_path.write_bytes(MINIMAL_PDF_BYTES)

        def _fake_pdf_anonymize(
            anonymizer_self: ReportAnonymizer,
            ctx: ImportContext,
        ) -> ImportContext:
            assert ctx.current_report is not None
            ctx.original_text = "pdf original text"
            ctx.anonymized_text = "pdf anonymized text"
            ctx.extracted_metadata = {}
            ctx.anonymized_path = None
            ctx.current_report.text = ctx.original_text
            ctx.current_report.anonymized_text = ctx.anonymized_text
            ctx.current_report.save(update_fields=["text", "anonymized_text"])
            sm = LxSM()
            if sensitive_meta_storage(sm, ctx.current_report):
                ctx.extracted_metadata = sm
                return ctx
            else:
                return ctx

        try:
            with patch.object(
                ReportAnonymizer,
                "anonymize_report",
                new=_fake_pdf_anonymize,
            ):
                report_obj = ReportImportService().import_and_anonymize(
                    file_path=pdf_path,
                    center_name=DEFAULT_CENTER_NAME,
                    retry=False,
                )

            assert isinstance(report_obj, RawPdfFile)
            report_obj.refresh_from_db()

            self.assertEqual(report_obj.text, "pdf original text")
            self.assertEqual(report_obj.anonymized_text, "pdf anonymized text")

            response = self.client.get(f"/api/media/pdfs/{report_obj.pk}/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["anonymized_text"], "pdf anonymized text")
            self.assertTrue(response.data["has_anonymized_text"])
        finally:
            pdf_path.unlink(missing_ok=True)

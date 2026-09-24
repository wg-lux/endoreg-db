from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch
import json
from typing import cast

import pymupdf
from django.contrib.auth.models import User
from django.test import TestCase
from lx_dtypes.models import SensitiveMeta

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.processing.report_processing.report_anonymization import (
    ReportAnonymizer,
)
from endoreg_db.models import RawPdfFile
from endoreg_db.services.report_import import ReportImportService
from endoreg_db.services.raw_pdf_files.types import PdfDocument
from endoreg_db.utils.hashs import get_file_hash
from tests.helpers.default_objects import DEFAULT_CENTER_NAME

MINIMAL_PDF_BYTES = ReportImportService._render_single_page_pdf(  # pyright: ignore[reportPrivateUsage]
    "Sample PDF"
)


class PdfMediaTextVisibilityTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from endoreg_db.helpers.data_load_orchestrator import load_base_db_data

        load_base_db_data()

    def setUp(self) -> None:
        self.client.force_login(
            User.objects.create_user(
                username="pdf-media-text-reader",
                is_staff=True,
            )
        )

    def test_txt_import_is_rendered_before_anonymization(self):
        txt_content = "patient report from txt\nline two"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
            txt_path = Path(tmp.name)
        txt_path.write_text(txt_content, encoding="utf-8")

        try:
            converted_path = ReportImportService()._create_temp_pdf_from_txt(  # pyright: ignore[reportPrivateUsage]
                txt_path
            )
            try:
                self.assertEqual(converted_path.suffix, ".pdf")
                document = cast(PdfDocument, pymupdf.open(filename=str(converted_path)))
                try:
                    self.assertFalse(document.is_repaired)
                    self.assertFalse(document.needs_pass)
                    self.assertGreater(document.page_count, 0)
                    rendered_text = " ".join(
                        document[index].get_text()
                        for index in range(document.page_count)
                    )
                    self.assertEqual(
                        " ".join(rendered_text.split()),
                        f"txt_sha256:{get_file_hash(txt_path)} "
                        + " ".join(txt_content.split()),
                    )
                finally:
                    document.close()
            finally:
                converted_path.unlink(missing_ok=True)
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
            ctx.extracted_metadata = SensitiveMeta()
            ctx.anonymized_path = ctx.file_path.with_name(
                f"{ctx.file_path.stem}-anonymized.pdf"
            )
            ctx.anonymized_path.write_bytes(MINIMAL_PDF_BYTES)
            ctx.current_report.text = ctx.original_text
            ctx.current_report.anonymized_text = ctx.anonymized_text
            ctx.current_report.save(update_fields=["text", "anonymized_text"])
            ctx.extracted_metadata = SensitiveMeta()
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
            data = json.loads(response.content)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(data["anonymized_text"], "pdf anonymized text")
            self.assertTrue(data["has_anonymized_text"])
        finally:
            pdf_path.unlink(missing_ok=True)

    def test_pdf_import_fails_closed_without_processed_output(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            pdf_path = Path(tmp.name)
        pdf_path.write_bytes(MINIMAL_PDF_BYTES)

        def _fake_without_output(
            anonymizer_self: ReportAnonymizer,
            ctx: ImportContext,
        ) -> ImportContext:
            ctx.anonymized_path = None
            return ctx

        try:
            with (
                patch.object(
                    ReportAnonymizer,
                    "anonymize_report",
                    new=_fake_without_output,
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "without an anonymized PDF output",
                ),
            ):
                ReportImportService().import_and_anonymize(
                    file_path=pdf_path,
                    center_name=DEFAULT_CENTER_NAME,
                    retry=False,
                )
        finally:
            pdf_path.unlink(missing_ok=True)

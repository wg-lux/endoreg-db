# tests/services/test_report_import_service_unit.py

import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.services.report_import import ReportImportService

ris = ReportImportService()
import_and_anonymize = ris.import_and_anonymize


class TestReportImportServiceUnit(unittest.TestCase):
    def test_import_and_anonymize_nonexistent_file(self):
        nonexistent_path = Path("/tmp/nonexistent_report.pdf")

        with self.assertRaises(FileNotFoundError):
            import_and_anonymize(
                file_path=nonexistent_path,
                center_name="dummy-center",
                retry=False,
            )

    def test_create_temp_pdf_from_txt(self):
        service = ReportImportService()
        txt_path = None
        temp_pdf = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
                txt_path = Path(tmp.name)
            txt_path.write_text("line one\nline two", encoding="utf-8")

            temp_pdf = service._create_temp_pdf_from_txt(txt_path)

            self.assertTrue(temp_pdf.exists())
            self.assertEqual(temp_pdf.suffix.lower(), ".pdf")
            self.assertTrue(temp_pdf.read_bytes().startswith(b"%PDF-1.4"))
        finally:
            if temp_pdf is not None and temp_pdf.exists():
                temp_pdf.unlink()
            if txt_path is not None and txt_path.exists():
                txt_path.unlink()

    @patch(
        "endoreg_db.import_files.report_import_service.ProcessingHistory.has_history_for_hash"
    )
    @patch(
        "endoreg_db.import_files.report_import_service.RawPdfFile.get_report_by_hash"
    )
    def test_get_existing_completed_report_returns_existing_instance(
        self,
        get_report_by_hash_mock,
        has_history_mock,
    ):
        service = ReportImportService()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            pdf_path = Path(tmp.name)
        pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

        try:
            ctx = ImportContext(
                file_path=pdf_path,
                center_name="dummy-center",
            )
            existing_report = object()
            has_history_mock.return_value = True
            get_report_by_hash_mock.return_value = existing_report

            result = service._get_existing_completed_report(ctx)

            self.assertIs(result, existing_report)
            has_history_mock.assert_called_once_with(
                file_hash=ctx.file_hash,
                success=True,
            )
            get_report_by_hash_mock.assert_called_once_with(ctx.file_hash)
        finally:
            if pdf_path.exists():
                pdf_path.unlink()

    def test_cleanup_duplicate_staging_deletes_import_source(self):
        service = ReportImportService()

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            import_dir = base / "report_import"
            import_dir.mkdir(parents=True, exist_ok=True)
            source_path = import_dir / "duplicate.pdf"
            source_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

            sensitive_path = base / "sensitive_copy.pdf"
            sensitive_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

            ctx = ImportContext(
                file_path=source_path,
                center_name="dummy-center",
                original_path=source_path,
            )
            ctx.sensitive_path = sensitive_path

            with patch(
                "endoreg_db.import_files.report_import_service.IMPORT_REPORT_DIR",
                import_dir,
            ):
                service._cleanup_duplicate_staging(ctx)

            self.assertFalse(source_path.exists())
            self.assertFalse(sensitive_path.exists())

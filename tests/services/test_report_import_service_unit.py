# tests/services/test_report_import_service_unit.py

import unittest
from pathlib import Path
import tempfile

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

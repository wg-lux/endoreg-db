# tests/services/test_report_import_service_unit.py

import unittest
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.services.report_import import ReportImportService
from endoreg_db.utils.file_operations import (
    atomic_write_file,
    ensure_directory,
    safe_unlink_file,
)

ris = ReportImportService()
import_and_anonymize = ris.import_and_anonymize


class TestReportImportServiceUnit(unittest.TestCase):
    def test_import_and_anonymize_nonexistent_file(self) -> None:
        nonexistent_path = Path("/tmp/nonexistent_report.pdf")

        with self.assertRaises(FileNotFoundError):
            import_and_anonymize(
                file_path=nonexistent_path,
                center_name="dummy-center",
                retry=False,
            )

    def test_create_temp_pdf_from_txt(self) -> None:
        service = ReportImportService()
        txt_path = None
        temp_pdf = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
                txt_path = Path(tmp.name)
            txt_content = b"line one\nline two"
            atomic_write_file(
                destination=txt_path,
                content=[txt_content],
                required_bytes=len(txt_content),
            )

            temp_pdf = service._create_temp_pdf_from_txt(txt_path)  # pyright: ignore[reportPrivateUsage]

            self.assertTrue(temp_pdf.exists())
            self.assertEqual(temp_pdf.suffix.lower(), ".pdf")
            self.assertTrue(temp_pdf.read_bytes().startswith(b"%PDF-1.4"))
        finally:
            if temp_pdf is not None and temp_pdf.exists():
                safe_unlink_file(temp_pdf)
            if txt_path is not None and txt_path.exists():
                safe_unlink_file(txt_path)

    @patch(
        "endoreg_db.import_files.report_import_service.ProcessingHistory.has_history_for_hash"
    )
    @patch("endoreg_db.import_files.report_import_service.get_raw_pdf_by_content_hash")
    def test_get_existing_completed_report_returns_existing_instance(
        self,
        get_report_by_hash_mock: Mock,
        has_history_mock: Mock,
    ) -> None:
        service = ReportImportService()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            pdf_path = Path(tmp.name)
        pdf_content = b"%PDF-1.4\n%%EOF\n"
        atomic_write_file(
            destination=pdf_path,
            content=[pdf_content],
            required_bytes=len(pdf_content),
        )

        try:
            ctx = ImportContext(
                file_path=pdf_path,
                center_name="dummy-center",
            )
            existing_report = object()
            has_history_mock.return_value = True
            get_report_by_hash_mock.return_value = existing_report

            result = service._get_existing_completed_report(ctx)  # pyright: ignore[reportPrivateUsage]

            self.assertIs(result, existing_report)
            has_history_mock.assert_called_once_with(
                file_hash=ctx.file_hash,
                success=True,
            )
            get_report_by_hash_mock.assert_called_once_with(ctx.file_hash)
        finally:
            if pdf_path.exists():
                safe_unlink_file(pdf_path)

    def test_cleanup_duplicate_staging_deletes_import_source(self) -> None:
        service = ReportImportService()

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            import_dir = base / "report_import"
            ensure_directory(import_dir)
            source_path = import_dir / "duplicate.pdf"
            pdf_content = b"%PDF-1.4\n%%EOF\n"
            atomic_write_file(
                destination=source_path,
                content=[pdf_content],
                required_bytes=len(pdf_content),
            )

            sensitive_dir = base / "sensitive_reports"
            ensure_directory(sensitive_dir)
            sensitive_path = sensitive_dir / "sensitive_copy.pdf"
            atomic_write_file(
                destination=sensitive_path,
                content=[pdf_content],
                required_bytes=len(pdf_content),
            )

            ctx = ImportContext(
                file_path=source_path,
                center_name="dummy-center",
                original_path=source_path,
            )
            ctx.sensitive_path = sensitive_path

            with (
                patch(
                    "endoreg_db.import_files.report_import_service._import_report_dir",
                    return_value=import_dir,
                ),
                patch(
                    "endoreg_db.import_files.report_import_service._sensitive_report_dir",
                    return_value=sensitive_dir,
                ),
            ):
                service._cleanup_duplicate_staging(ctx)  # pyright: ignore[reportPrivateUsage]

            self.assertFalse(source_path.exists())
            self.assertFalse(sensitive_path.exists())

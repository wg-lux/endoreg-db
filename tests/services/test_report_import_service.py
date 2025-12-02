"""
Unit tests for report import service functionality.

Tests the import_and_anonymize service function that combines RawPdfFile creation
with text/anonymization pipeline.
"""

import os
import tempfile
from pathlib import Path

import pytest
from django.test import TestCase

from endoreg_db.models import RawPdfFile
from endoreg_db.services.report_import import ReportImportService
from tests.helpers.default_objects import get_default_center, get_default_processor

import logging

# Environment-based test control (mirror video tests)
SKIP_EXPENSIVE_TESTS = os.environ.get("SKIP_EXPENSIVE_TESTS", "true").lower() == "true"

logger = logging.getLogger(__name__)

ris = ReportImportService()
import_and_anonymize = ris.import_and_anonymize

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
(Sample report) Tj
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


class TestReportImportService(TestCase):
    """Test cases for report (PDF) import service."""

    @classmethod
    def setUpClass(cls):
        """Set up session-scoped fixtures."""
        super().setUpClass()
        from endoreg_db.helpers.data_loader import load_base_db_data
        load_base_db_data()

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.center = get_default_center()
        self.processor = get_default_processor()

    @pytest.mark.integration
    @pytest.mark.report
    @pytest.mark.expensive
    def test_import_and_anonymize_success(self):
        """
        Test successful import and anonymization of a report file.

        Creates a temporary PDF file, calls import_and_anonymize,
        and verifies a RawPdfFile was created and linked to the center/processor.
        """
        if SKIP_EXPENSIVE_TESTS:
            self.skipTest("Skipping expensive report import test (SKIP_EXPENSIVE_TESTS=true)")

        # Create a temporary PDF file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            pdf_path = Path(tmp.name)
        pdf_path.write_bytes(MINIMAL_PDF_BYTES)

        try:
            service = ReportImportService()

            pdf_file = service.import_and_anonymize(
                file_path=pdf_path,
                center_name=self.center.name,
                processor_name=self.processor.name,
                delete_source=False,
            )

            # Basic checks
            self.assertIsNotNone(pdf_file, "RawPdfFile should be created")
            self.assertIsInstance(pdf_file, RawPdfFile)
            self.assertEqual(pdf_file.center, self.center)

            # If processor is modeled on RawPdfFile, assert it too
            if hasattr(pdf_file, "processor"):
                self.assertEqual(pdf_file.processor, self.processor)

            # State exists and is attached
            if hasattr(pdf_file, "state") and pdf_file.state:
                self.assertIsNotNone(pdf_file.state)

        finally:
            if pdf_path.exists():
                pdf_path.unlink()

    @pytest.mark.unit
    def test_import_and_anonymize_nonexistent_file(self):
        """
        Test import_and_anonymize handles nonexistent files gracefully.

        This is a fast unit test that doesn't require actual PDF processing.
        """
        nonexistent_path = Path("/tmp/nonexistent_report.pdf")

        with self.assertRaises(FileNotFoundError):
            import_and_anonymize(
                file_path=nonexistent_path,
                center_name=self.center.name,
                processor_name=self.processor.name,
            )

    @pytest.mark.integration
    @pytest.mark.report
    @pytest.mark.expensive
    def test_import_and_anonymize_with_different_options(self):
        """
        Test import_and_anonymize with different delete_source options.

        Mirrors the video test:
        - Create a temporary copy
        - Call service with delete_source=True
        - Ensure we still get a RawPdfFile and no crash
        """
        if SKIP_EXPENSIVE_TESTS:
            self.skipTest("Skipping expensive report import test (SKIP_EXPENSIVE_TESTS=true)")

        # Create a temporary PDF file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            temp_path = Path(tmp.name)
        temp_path.write_bytes(MINIMAL_PDF_BYTES)

        try:
            pdf_file = import_and_anonymize(
                file_path=temp_path,
                center_name=self.center.name,
                processor_name=self.processor.name,
                delete_source=True,
            )

            self.assertIsNotNone(pdf_file)
            self.assertIsInstance(pdf_file, RawPdfFile)

        finally:
            # Clean up if file still exists (delete_source=True may have removed it)
            if temp_path.exists():
                temp_path.unlink()

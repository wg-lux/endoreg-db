"""
Unit tests for report import service functionality.

Tests the import_and_anonymize service function that combines RawPdfFile creation
with text/anonymization pipeline.
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import cast

import pytest
from django.test import TestCase
from django.utils import timezone

from endoreg_db.models import Center, PatientExamination, RawPdfFile
from endoreg_db.services.report_import import ReportImportService
from endoreg_db.services.report_materialization import (
    upsert_anonym_examination_report_from_pdf,
)
from endoreg_db.utils.file_operations import (
    atomic_write_file,
    safe_unlink_file,
)
from tests.helpers.default_objects import get_default_center, get_default_processor

# Environment-based test control (mirror video tests)
SKIP_EXPENSIVE_TESTS = os.environ.get("SKIP_EXPENSIVE_TESTS", "true").lower() == "true"
pytestmark = pytest.mark.expensive

logger = logging.getLogger(__name__)

MINIMAL_report_BYTES = b"""%report-1.4
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


def _report_pipeline_ready() -> bool:
    return bool(os.environ.get("EXPECTED_MODEL_SHA256"))


class TestReportImportService(TestCase):
    """Test cases for report (report) import service."""

    @classmethod
    def setUpClass(cls):
        """Set up session-scoped fixtures."""
        super().setUpClass()
        from endoreg_db.helpers.data_load_orchestrator import load_base_db_data

        load_base_db_data()

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.center = get_default_center()
        self.processor = get_default_processor()

    @pytest.mark.integration
    def test_import_and_anonymize_success(self):
        """
        Test successful import and anonymization of a report file.

        Creates a temporary report file, calls import_and_anonymize,
        and verifies a RawPdfFile was created and linked to the center/processor.
        """
        if SKIP_EXPENSIVE_TESTS:
            self.skipTest(
                "Skipping expensive report import test (SKIP_EXPENSIVE_TESTS=true)"
            )
        if not _report_pipeline_ready():
            self.skipTest(
                "Skipping expensive report import test (EXPECTED_MODEL_SHA256 is not configured)."
            )

        # Create a temporary report file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            pdf_path = Path(tmp.name)
        atomic_write_file(destination=pdf_path, content=(MINIMAL_report_BYTES,))

        try:
            service = ReportImportService()

            pdf_file = service.import_and_anonymize(
                file_path=pdf_path,
                center_name=self.center.name,
                retry=False,
            )

            # Basic checks
            self.assertIsNotNone(pdf_file, "RawPdfFile should be created")
            self.assertIsInstance(pdf_file, RawPdfFile)
            self.assertIsNotNone(pdf_file)
            assert pdf_file is not None
            self.assertIsInstance(pdf_file.center, Center)
            self.assertEqual(pdf_file.center, self.center)

            # State exists and is attached
            assert pdf_file is not None
            if hasattr(pdf_file, "state") and pdf_file.state:
                self.assertIsNotNone(pdf_file.state)

        finally:
            safe_unlink_file(pdf_path, missing_ok=True)

    @pytest.mark.integration
    def test_imported_raw_pdf_can_link_to_anonym_examination_report(self):
        """
        Verify that a successfully imported RawPdfFile can be promoted into
        a report_file artifact (AnonymExaminationReport) later in the workflow.
        """
        if SKIP_EXPENSIVE_TESTS:
            self.skipTest(
                "Skipping expensive report import test (SKIP_EXPENSIVE_TESTS=true)"
            )
        if not _report_pipeline_ready():
            self.skipTest(
                "Skipping expensive report import test (EXPECTED_MODEL_SHA256 is not configured)."
            )

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            pdf_path = Path(tmp.name)
        atomic_write_file(
            destination=pdf_path,
            content=(MINIMAL_report_BYTES + b"\n%report-link-check\n",),
        )

        try:
            service = ReportImportService()
            raw_pdf = service.import_and_anonymize(
                file_path=pdf_path,
                center_name=self.center.name,
                retry=False,
            )

            self.assertIsInstance(raw_pdf, RawPdfFile)
            assert isinstance(raw_pdf, RawPdfFile)
            raw_pdf.refresh_from_db()

            self.assertIsNotNone(raw_pdf.pk)
            self.assertTrue(bool(raw_pdf.file))
            self.assertTrue(bool(raw_pdf.processed_file))
            self.assertIsNotNone(raw_pdf.sensitive_meta_id)
            sensitive_meta = raw_pdf.sensitive_meta
            assert sensitive_meta is not None
            self.assertIsNotNone(sensitive_meta.pseudo_patient_id)

            raw_pdf.examination = PatientExamination.objects.create(
                patient=sensitive_meta.pseudo_patient
            )
            raw_pdf.save(update_fields=["examination"])

            report_obj, _created = upsert_anonym_examination_report_from_pdf(
                pdf=raw_pdf,
                payload={"anonymized_text": "validated report text"},
                document_type_name="report_draft",
                validated_at_iso=timezone.now().isoformat(),
            )

            raw_pdf.refresh_from_db()
            self.assertIsNotNone(raw_pdf.anonym_examination_report_id)
            self.assertEqual(raw_pdf.anonym_examination_report_id, report_obj.pk)
            linked_raw_pdf = cast(RawPdfFile, getattr(report_obj, "raw_pdf_file"))
            self.assertEqual(linked_raw_pdf.pk, raw_pdf.pk)
            report_type = report_obj.type
            assert report_type is not None
            self.assertEqual(report_type.name, "report_draft")

        finally:
            safe_unlink_file(pdf_path, missing_ok=True)

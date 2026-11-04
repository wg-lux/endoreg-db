from tests.helpers.pdf import render_single_page_pdf

"""
Integration tests for report import service functionality.

Tests the import_and_anonymize service function that combines RawPdfFile creation
with text/anonymization pipeline. These run the real OCR/anonymizer runtime and
are skipped at collection by the ``expensive`` marker when
SKIP_EXPENSIVE_TESTS=true (see tests/conftest.py).
"""

import tempfile
from datetime import date
from pathlib import Path
from typing import Protocol, cast

import pytest
from django.test import TransactionTestCase
from django.utils import timezone

from endoreg_db.models import Center, PatientExamination, RawPdfFile
from endoreg_db.services.reports.import_service import ReportImportService
from endoreg_db.services.reports.materialization import (
    upsert_anonym_examination_report_from_pdf,
)
from endoreg_db.utils.file_operations import atomic_write_file
from tests.helpers.default_objects import (
    DEFAULT_PATIENT_BIRTH_DATE,
    DEFAULT_PATIENT_FIRST_NAME,
    DEFAULT_PATIENT_GENDER_NAME,
    DEFAULT_PATIENT_LAST_NAME,
    get_default_center,
    get_default_processor,
)

pytestmark = pytest.mark.expensive

# Realistic text layers without patient identity (no names, dates or numbers).
_IDENTITY_FREE_FINDING = "Koloskopie bis Zökum, Schleimhaut unauffällig, keine Polypen."
_IDENTITY_FREE_REPAIR_FINDING = (
    "Koloskopie bis Zökum, Schleimhaut unauffällig, kein Polyp."
)
# lx_anonymizer applies OCR below 50 text-layer characters (literal in
# report_reader.reader) and LLM metadata extraction from
# REPORT_LLM_MIN_TEXT_LENGTH on; neither path is under test here.
_OCR_FALLBACK_BELOW = 50


class TestReportImportService(TransactionTestCase):
    """Test cases for report import service."""

    def setUp(self):
        """Load fixtures outside a test-wide transaction for heartbeat threads."""
        super().setUp()
        from endoreg_db.helpers.data_load_orchestrator import load_base_db_data

        load_base_db_data()
        self.center = get_default_center()
        self.processor = get_default_processor()
        self.source_dir = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def _write_report(self, text: str) -> Path:
        """Write a valid single-page PDF; distinct text yields a distinct hash.

        The text layer stays between the OCR and LLM thresholds so the import
        uses deterministic spaCy/regex metadata extraction and avoids ~12 s of
        OCR or an LLM call per import.
        """
        from lx_anonymizer.config import settings as anonymizer_settings

        length = len(text.strip())
        assert (
            _OCR_FALLBACK_BELOW
            <= length
            < anonymizer_settings.REPORT_LLM_MIN_TEXT_LENGTH
        ), f"text layer of {length} characters leaves the spaCy/regex window"
        pdf_path = self.source_dir / "report.pdf"
        atomic_write_file(
            destination=pdf_path,
            content=(render_single_page_pdf(text),),
        )
        return pdf_path

    @pytest.mark.integration
    def test_import_and_anonymize_success(self):
        """
        Import an identity-free report, verify the stored artifacts, then
        promote the RawPdfFile into an AnonymExaminationReport.
        """
        pdf_path = self._write_report(_IDENTITY_FREE_FINDING)

        raw_pdf = ReportImportService().import_and_anonymize(
            file_path=pdf_path,
            center_name=self.center.name,
            retry=False,
        )

        assert isinstance(raw_pdf, RawPdfFile)
        raw_pdf.refresh_from_db()
        self.assertIsInstance(raw_pdf.center, Center)
        self.assertEqual(raw_pdf.center, self.center)
        self.assertTrue(bool(raw_pdf.file))
        self.assertTrue(bool(raw_pdf.processed_file))
        assert raw_pdf.state is not None
        self.assertTrue(raw_pdf.state.anonymized)
        sensitive_meta = raw_pdf.sensitive_meta
        assert sensitive_meta is not None
        # This document contains no identity; resolve reviewed metadata before
        # promoting the imported artifact into a patient-linked report.
        self.assertIsNone(sensitive_meta.pseudo_patient_id)
        self.assertIsNone(sensitive_meta.pseudo_examination_id)
        sensitive_meta.update_from_dict(
            {
                "patient_first_name": DEFAULT_PATIENT_FIRST_NAME,
                "patient_last_name": DEFAULT_PATIENT_LAST_NAME,
                "patient_dob": DEFAULT_PATIENT_BIRTH_DATE,
                "patient_gender": DEFAULT_PATIENT_GENDER_NAME,
                "examination_date": date(2024, 2, 15),
            }
        )
        sensitive_meta.refresh_from_db()
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
        self.assertEqual(raw_pdf.anonym_examination_report_id, report_obj.pk)
        linked_raw_pdf = cast(RawPdfFile, getattr(report_obj, "raw_pdf_file"))
        self.assertEqual(linked_raw_pdf.pk, raw_pdf.pk)
        report_type = cast(_AnonymizedReportType, getattr(report_obj, "type"))
        assert report_type is not None
        self.assertEqual(report_type.name, "report_draft")

    @pytest.mark.integration
    def test_success_history_with_missing_processed_pdf_is_repaired(self):
        pdf_path = self._write_report(_IDENTITY_FREE_REPAIR_FINDING)

        first = ReportImportService().import_and_anonymize(
            file_path=pdf_path,
            center_name=self.center.name,
            retry=False,
        )
        assert isinstance(first, RawPdfFile)
        first.refresh_from_db()
        assert first.processed_file
        first.processed_file.delete(save=True)

        repaired = ReportImportService().import_and_anonymize(
            file_path=pdf_path,
            center_name=self.center.name,
            retry=False,
        )

        assert isinstance(repaired, RawPdfFile)
        repaired.refresh_from_db()
        assert repaired.pk == first.pk
        assert repaired.processed_file
        processed_name = repaired.processed_file.name
        assert processed_name is not None
        assert repaired.processed_file.storage.exists(processed_name)
        assert repaired.state is not None
        assert repaired.state.anonymized is True
        assert len(repaired.state.processed_file_sha256) == 64


class _AnonymizedReportType(Protocol):
    name: str

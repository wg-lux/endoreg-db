from __future__ import annotations

from tests.helpers.pdf import render_single_page_pdf

import tempfile
from pathlib import Path
from unittest.mock import patch
import json
from typing import cast

import pymupdf
from django.contrib.auth.models import User
from django.test import TransactionTestCase
from lx_dtypes.models import SensitiveMeta
from lx_dtypes.models.contracts.report_anonymization import (
    ReportAnonymizationProvenance,
    ReportAnonymizationRequest,
    ReportAnonymizationResult,
    ReportArtifactValidation,
    ReportExtractionEvidence,
    ReportExtractionReceipt,
)

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.processing.report_processing.report_anonymization import (
    ReportAnonymizer,
)
from endoreg_db.models import RawPdfFile
from endoreg_db.services.reports.import_service import ReportImportService
from endoreg_db.services.raw_pdf_files.types import PdfDocument
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.file_operations import atomic_write_file
from tests.helpers.default_objects import DEFAULT_CENTER_NAME

MINIMAL_PDF_BYTES = render_single_page_pdf("Sample PDF")


class _ReportReader:
    llm_available = False

    def process_report(
        self, request: ReportAnonymizationRequest
    ) -> ReportAnonymizationResult:
        artifact = request.output_directory / "anonymized.pdf"
        atomic_write_file(destination=artifact, content=(MINIMAL_PDF_BYTES,))
        return ReportAnonymizationResult(
            attempt_id=request.attempt_id,
            source_sha256=request.source_sha256,
            original_text="pdf original text",
            anonymized_text="pdf anonymized text",
            extracted_metadata=SensitiveMeta(),
            artifact_path=artifact,
            artifact_sha256=get_file_hash(artifact),
            artifact_size_bytes=len(MINIMAL_PDF_BYTES),
            artifact_validation=ReportArtifactValidation(page_count=1, repaired=False),
            extraction_evidence=ReportExtractionEvidence(
                outcome="fallback",
                backend="spacy_regex",
                finding_count=0,
                provider_attempted=False,
            ),
            provenance=ReportAnonymizationProvenance(
                anonymizer_version="test", used_llm=False, deterministic=True
            ),
        )


class PdfMediaTextVisibilityTests(TransactionTestCase):
    def setUp(self) -> None:
        super().setUp()
        from endoreg_db.helpers.data_load_orchestrator import load_base_db_data

        load_base_db_data()
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
            converted_path = ReportImportService.create_pdf_from_text(txt_path)
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
                    # The rendered report carries no digest of the raw source.
                    self.assertEqual(
                        " ".join(rendered_text.split()),
                        " ".join(txt_content.split()),
                    )
                    self.assertNotIn(get_file_hash(txt_path), rendered_text)
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

        try:
            with patch.object(
                ReportAnonymizer,
                "_instantiate_report_reader",
                return_value=_ReportReader(),
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
        source_hash = get_file_hash(pdf_path)
        anonymize_report = ReportAnonymizer.anonymize_report

        def _fake_without_output(
            anonymizer_self: ReportAnonymizer,
            ctx: ImportContext,
        ) -> ImportContext:
            ctx = anonymize_report(anonymizer_self, ctx)
            assert ctx.current_report is not None
            assert ctx.current_report.sensitive_meta_id is not None
            receipt = ReportExtractionReceipt.model_validate(
                (ctx.current_report.raw_meta or {})["report_extraction_receipt"]
            )
            self.assertEqual(receipt.attempt_id, ctx.attempt_id)
            self.assertEqual(receipt.source_sha256, source_hash)
            # Lose only the prepared output reference, after valid extraction.
            ctx.anonymized_path = None
            return ctx

        try:
            with (
                patch.object(
                    ReportAnonymizer,
                    "_instantiate_report_reader",
                    return_value=_ReportReader(),
                ),
                patch.object(
                    ReportAnonymizer,
                    "anonymize_report",
                    new=_fake_without_output,
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "Prepared import lacks its artifact, source or sensitive metadata",
                ),
            ):
                ReportImportService().import_and_anonymize(
                    file_path=pdf_path,
                    center_name=DEFAULT_CENTER_NAME,
                    retry=False,
                )
            report = RawPdfFile.objects.get(pdf_hash=source_hash)
            self.assertIsNotNone(report.sensitive_meta_id)
            self.assertIn("report_extraction_receipt", report.raw_meta or {})
            self.assertFalse(report.processed_file)
            assert report.state is not None
            self.assertFalse(report.state.anonymized)
        finally:
            pdf_path.unlink(missing_ok=True)

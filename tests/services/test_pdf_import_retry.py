"""
Tests for PdfReimportView to ensure ReportImportService.import_and_anonymize
is called correctly when reimporting reports from reimport.py.
"""

from pathlib import Path
from unittest.mock import patch

import logging
import pytest
from django.core.files.base import ContentFile
from rest_framework import status
from rest_framework.test import APIRequestFactory

from endoreg_db.models import Center, RawPdfFile, SensitiveMeta
from endoreg_db.utils.hashs import get_pdf_hash
from endoreg_db.views.pdf.reimport import PdfReimportView  # adjust path if needed


@pytest.mark.django_db
class TestPdfReimportView:
    @pytest.fixture
    def center(self):
        return Center.objects.create(
            name="university_hospital_wuerzburg",
            display_name="University Hospital Würzburg",
        )

    @pytest.fixture
    def sample_pdf_content(self):
        return b"%PDF-1.4\nTest report content\n%%EOF\n"

    @pytest.fixture
    def api_factory(self):
        return APIRequestFactory()

    def test_reimport_calls_import_and_anonymize_with_raw_file(
        self, center, sample_pdf_content, api_factory, tmp_path
    ):
        """
        Happy path:
        - PdfReimportView resolves raw file via get_raw_file_path()
        - Calls ReportImportService.import_and_anonymize with:
          - file_path=raw_file_path
          - center_name=pdf.center.name
          - retry=True
          - delete_source=False
        - Returns 200 and expected response payload
        """
        # create raw file
        raw_file = tmp_path / "raw_pdfs" / "original.pdf"
        raw_file.parent.mkdir(parents=True, exist_ok=True)
        raw_file.write_bytes(sample_pdf_content)

        pdf_hash = get_pdf_hash(raw_file)

        # create RawPdfFile bound to center; file field is not used by view
        pdf = RawPdfFile.objects.create(
            pdf_hash=pdf_hash,
            center=center,
            text=None,
        )
        pdf.file.save("sensitive.pdf", ContentFile(sample_pdf_content), save=True)

        view = PdfReimportView.as_view()
        request = api_factory.post("/api/media/reports/%d/reimport/" % pdf.pk, {})

        # patch get_raw_file_path to return our raw_file (simulating correct discovery)
        # patch import_and_anonymize to avoid running the full pipeline
        with patch(
            "endoreg_db.views.media.reimport.RawPdfFile.get_raw_file_path",
            return_value=raw_file,
        ) as mock_get_raw, patch(
            "endoreg_db.services.report_import.ReportImportService.import_and_anonymize"
        ) as mock_import:
            mock_import.return_value = pdf  # simulate successful reimport

            response = view(request, pk=pdf.pk)

        # assertions on service call
        mock_get_raw.assert_called_once()
        mock_import.assert_called_once()

        call_kwargs = mock_import.call_args.kwargs
        assert call_kwargs["file_path"] == raw_file
        assert call_kwargs["center_name"] == pdf.center.name
        # delete_source is forced to False in reimport
        assert call_kwargs["delete_source"] is False
        # reimport always marks retry=True
        assert call_kwargs["retry"] is True

        # response checks
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["pdf_id"] == pdf.pk
        assert data["pdf_hash"] == str(pdf.pdf_hash)
        assert data["status"] == "done"

    def test_reimport_missing_raw_file_returns_404(
        self, center, api_factory, caplog
    ):
        """
        - RawPdfFile exists, but get_raw_file_path() returns None or non-existing path
        - View should return 404 and a clear error message
        """
        pdf = RawPdfFile.objects.create(
            pdf_hash="missing-raw-file-hash",
            center=center,
            text=None,
        )

        view = PdfReimportView.as_view()
        request = api_factory.post(f"/api/media/reports/{pdf.pk}/reimport/", {})

        caplog.set_level(logging.ERROR)

        with patch(
            "endoreg_db.views.media.reimport.RawPdfFile.get_raw_file_path",
            return_value=None,
        ):
            response = view(request, pk=pdf.pk)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert "Raw report file not found" in data["error"]

        # optional: check log message content
        assert any(
            "Raw report file not found for hash" in rec.message
            for rec in caplog.records
        )

    def test_reimport_propagates_processing_error_from_service(
        self, center, sample_pdf_content, api_factory, tmp_path
    ):
        """
        - Raw file exists
        - ReportImportService.import_and_anonymize raises an exception
        - View should respond with 500 and error_type='processing_error'
        """
        raw_file = tmp_path / "raw_pdfs" / "original.pdf"
        raw_file.parent.mkdir(parents=True, exist_ok=True)
        raw_file.write_bytes(sample_pdf_content)

        pdf_hash = get_pdf_hash(raw_file)

        pdf = RawPdfFile.objects.create(
            pdf_hash=pdf_hash,
            center=center,
            text=None,
        )

        view = PdfReimportView.as_view()
        request = api_factory.post(f"/api/media/reports/{pdf.pk}/reimport/", {})

        with patch(
            "endoreg_db.views.media.reimport.RawPdfFile.get_raw_file_path",
            return_value=raw_file,
        ), patch(
            "endoreg_db.services.report_import.ReportImportService.import_and_anonymize",
            side_effect=RuntimeError("processing failed"),
        ):
            response = view(request, pk=pdf.pk)

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        data = response.json()
        assert data["error_type"] == "processing_error"
        assert "processing failed" in data["error"]

    def test_reimport_clears_existing_sensitive_meta(
        self, center, sample_pdf_content, api_factory, tmp_path
    ):
        """
        Sanity check:
        - If pdf has existing SensitiveMeta, reimport clears it before calling service.
        - After service call, pdf is refreshed and has new SensitiveMeta.
        """
        raw_file = tmp_path / "raw_pdfs" / "original.pdf"
        raw_file.parent.mkdir(parents=True, exist_ok=True)
        raw_file.write_bytes(sample_pdf_content)

        pdf_hash = get_pdf_hash(raw_file)

        pdf = RawPdfFile.objects.create(
            pdf_hash=pdf_hash,
            center=center,
            text="old text",
        )
        old_meta = SensitiveMeta.objects.create(
            center=center,
            patient_first_name="Old",
            patient_last_name="Meta",
        )
        pdf.sensitive_meta = old_meta
        pdf.save(update_fields=["sensitive_meta"])

        view = PdfReimportView.as_view()
        request = api_factory.post(f"/api/media/reports/{pdf.pk}/reimport/", {})

        # prepare new meta that service would attach
        new_meta = SensitiveMeta.objects.create(
            center=center,
            patient_first_name="New",
            patient_last_name="Meta",
        )

        with patch(
            "endoreg_db.views.media.reimport.RawPdfFile.get_raw_file_path",
            return_value=raw_file,
        ), patch(
            "endoreg_db.services.report_import.ReportImportService.import_and_anonymize"
        ) as mock_import:
            # simulate that the pipeline eventually attaches new meta + text
            def _fake_import(file_path, center_name, delete_source, retry):
                # we mimic what the service would have done by the time
                # the view calls pdf.refresh_from_db()
                pdf.text = "new text"
                pdf.sensitive_meta = new_meta
                pdf.state.anonymized = True
                pdf.save()
                return pdf

            mock_import.side_effect = _fake_import

            response = view(request, pk=pdf.pk)

        assert response.status_code == status.HTTP_200_OK
        pdf.refresh_from_db()
        assert pdf.sensitive_meta == new_meta
        assert pdf.text == "new text"
        assert pdf.state.anonymized is True
        # old meta should be gone
        assert not SensitiveMeta.objects.filter(pk=old_meta.pk).exists()

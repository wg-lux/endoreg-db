from __future__ import annotations

# pyright: reportUnknownMemberType=false

import json
from typing import TypedDict, cast
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client as DjangoClient

from endoreg_db.models.media.pdf.pdf_processing_history import PdfProcessingHistory
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.operation_log import OperationLog
from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.storage import ensure_local_file

MINIMAL_PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
MINIMAL_PDF_MANIFEST = {
    "version": 1,
    "pages": [
        {
            "page": 1,
            "boxes": [
                {
                    "x": 0.12,
                    "y": 0.34,
                    "width": 0.20,
                    "height": 0.05,
                }
            ],
        }
    ],
    "normalized": True,
}


class PdfProcessingHistoryResponseItem(TypedDict):
    operation: str
    revision_id: int


@pytest.mark.django_db(transaction=True)
class TestPdfRedactionEndpoints:
    @pytest.fixture(autouse=True)
    def _ensure_pdf_processing_history_table(self):
        existing_tables = set(connection.introspection.table_names())
        if "pdf_processing_history" in existing_tables:
            return
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(PdfProcessingHistory)

    def _create_pdf(self) -> RawPdfFile:
        raw_file = SimpleUploadedFile(
            "source.pdf",
            MINIMAL_PDF_BYTES,
            content_type="application/pdf",
        )
        state = RawPdfState.objects.create(
            anonymized=True,
            sensitive_meta_processed=True,
            anonymization_validated=True,
        )
        return RawPdfFile.objects.create(
            pdf_hash=f"pdf-redaction-{uuid4().hex}",
            file=raw_file,
            state=state,
        )

    def _source_sha256(self, pdf: RawPdfFile) -> str:
        with ensure_local_file(pdf.file) as local_path:
            return sha256_file(local_path)

    def test_apply_redactions_persists_processed_file_and_history(self):
        client = DjangoClient()
        pdf = self._create_pdf()
        source_sha256 = self._source_sha256(pdf)

        redacted_file = SimpleUploadedFile(
            "redacted.pdf",
            MINIMAL_PDF_BYTES + b"\n%redacted\n",
            content_type="application/pdf",
        )
        payload = {
            "file": redacted_file,
            "redaction_manifest": json.dumps(MINIMAL_PDF_MANIFEST),
            "source_type": "raw",
            "note": "mask patient footer",
            "client_source_sha256": source_sha256,
        }

        response = client.post(
            f"/api/media/pdfs/{pdf.pk}/apply-redactions/",
            data=payload,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["file_id"] == pdf.pk
        assert data["status"] == "done_processing_anonymization"
        assert data["anonymization_validated"] is False
        assert (
            data["processed_stream_url"]
            == f"/endoreg-api/media/pdfs/{pdf.pk}/stream/?type=processed"
        )

        pdf.refresh_from_db()
        raw_file_name = pdf.file.name
        processed_file_name = pdf.processed_file.name
        assert raw_file_name is not None
        assert processed_file_name is not None
        assert raw_file_name.endswith(".pdf")
        assert processed_file_name.endswith(".pdf")
        assert pdf.state is not None
        assert pdf.state.anonymized is True
        assert pdf.state.anonymization_validated is False
        assert pdf.state.sensitive_meta_processed is True

        history = PdfProcessingHistory.objects.filter(pdf=pdf).first()
        assert history is not None
        assert history.operation == "pdf_redaction"
        assert history.note == "mask patient footer"
        assert history.redaction_manifest == MINIMAL_PDF_MANIFEST

        operation_log = OperationLog.objects.filter(
            action="pdf_redaction",
            resource_type="pdf",
            resource_id=pdf.pk,
        ).first()
        assert operation_log is not None

        stream_response = client.get(f"/api/media/pdfs/{pdf.pk}/stream/?type=processed")
        assert stream_response.status_code == 200
        assert stream_response["Content-Type"] == "application/pdf"

        history_response = client.get(f"/api/media/pdfs/{pdf.pk}/processing-history/")
        assert history_response.status_code == 200
        history_data = cast(
            list[PdfProcessingHistoryResponseItem],
            history_response.json(),
        )
        assert isinstance(history_data, list)
        assert len(history_data) == 1
        assert history_data[0]["operation"] == "pdf_redaction"
        assert history_data[0]["revision_id"] == history.pk

    def test_apply_redactions_rejects_wrong_media_type(self):
        client = DjangoClient()
        pdf = self._create_pdf()
        bad_file = SimpleUploadedFile(
            "redacted.pdf",
            MINIMAL_PDF_BYTES,
            content_type="text/plain",
        )
        payload = {
            "file": bad_file,
            "redaction_manifest": json.dumps(MINIMAL_PDF_MANIFEST),
            "source_type": "raw",
        }
        response = client.post(
            f"/api/media/pdfs/{pdf.pk}/apply-redactions/",
            data=payload,
        )
        assert response.status_code == 415

    def test_apply_redactions_rejects_invalid_pdf_payload(self):
        client = DjangoClient()
        pdf = self._create_pdf()
        invalid_pdf = SimpleUploadedFile(
            "redacted.pdf",
            b"this is not a pdf",
            content_type="application/pdf",
        )
        payload = {
            "file": invalid_pdf,
            "redaction_manifest": json.dumps(MINIMAL_PDF_MANIFEST),
            "source_type": "raw",
        }
        response = client.post(
            f"/api/media/pdfs/{pdf.pk}/apply-redactions/",
            data=payload,
        )
        assert response.status_code == 400

    def test_apply_redactions_rejects_source_hash_mismatch(self):
        client = DjangoClient()
        pdf = self._create_pdf()
        redacted_file = SimpleUploadedFile(
            "redacted.pdf",
            MINIMAL_PDF_BYTES,
            content_type="application/pdf",
        )
        payload = {
            "file": redacted_file,
            "redaction_manifest": json.dumps(MINIMAL_PDF_MANIFEST),
            "source_type": "raw",
            "client_source_sha256": "0" * 64,
        }
        response = client.post(
            f"/api/media/pdfs/{pdf.pk}/apply-redactions/",
            data=payload,
        )
        assert response.status_code == 409

    def test_apply_redactions_rejects_oversized_upload(self):
        client = DjangoClient()
        pdf = self._create_pdf()
        redacted_file = SimpleUploadedFile(
            "redacted.pdf",
            MINIMAL_PDF_BYTES + b"X" * 16,
            content_type="application/pdf",
        )
        payload = {
            "file": redacted_file,
            "redaction_manifest": json.dumps(MINIMAL_PDF_MANIFEST),
            "source_type": "raw",
        }
        with patch(
            "endoreg_db.views.report.pdf_redaction.MAX_REDACTION_UPLOAD_BYTES", 8
        ):
            response = client.post(
                f"/api/media/pdfs/{pdf.pk}/apply-redactions/",
                data=payload,
            )
        assert response.status_code == 413

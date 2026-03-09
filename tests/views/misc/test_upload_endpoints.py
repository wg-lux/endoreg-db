from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase


MINIMAL_PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


class UploadEndpointTests(TestCase):
    def test_upload_rejects_missing_file(self):
        response = self.client.post("/api/upload/", data={})
        assert response.status_code == 400, response.content
        assert "No file provided" in response.json()["error"]

    def test_upload_status_returns_404_for_unknown_job(self):
        response = self.client.get(f"/api/upload/{uuid4()}/status/")
        assert response.status_code == 404, response.content

    def test_upload_pdf_creates_job_and_status_is_available(self):
        uploaded = SimpleUploadedFile(
            name="upload-test.pdf",
            content=MINIMAL_PDF_BYTES,
            content_type="application/pdf",
        )

        with patch("endoreg_db.views.misc.upload_views.CELERY_AVAILABLE", False):
            response = self.client.post("/api/upload/", data={"file": uploaded})

        assert response.status_code == 201, response.content
        payload = response.json()
        assert "upload_id" in payload
        assert "status_url" in payload
        assert payload["status_url"].endswith(f"/upload/{payload['upload_id']}/status/")

        status_response = self.client.get(payload["status_url"])
        assert status_response.status_code == 200, status_response.content
        status_payload = status_response.json()
        assert status_payload["id"] == payload["upload_id"]
        assert status_payload["status"] == "processing"

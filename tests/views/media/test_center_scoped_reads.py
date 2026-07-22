from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User
from django.test import TestCase

from endoreg_db.models import Center, Examination, Patient, RawPdfFile, VideoFile

MINIMAL_PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


class CenterScopedReadTests(TestCase):
    center_a: Center
    center_b: Center
    patient: Patient
    examination: Examination
    report: RawPdfFile
    video: VideoFile

    def setUp(self) -> None:
        self.center_a = Center.objects.create(
            name=f"center-a-{uuid4().hex[:8]}",
            display_name="Center A",
        )
        self.center_b = Center.objects.create(
            name=f"center-b-{uuid4().hex[:8]}",
            display_name="Center B",
        )
        self.patient = Patient.objects.create(
            first_name="Scope",
            last_name="Patient",
            center=self.center_a,
            is_real_person=False,
            patient_hash=f"scope-patient-{uuid4().hex}",
        )
        self.examination = Examination.objects.create(
            name=f"scope-exam-{uuid4().hex[:8]}"
        )
        self.report = RawPdfFile.objects.create(
            pdf_hash=f"scope-pdf-{uuid4().hex}",
            file=SimpleUploadedFile(
                name=f"scope-{uuid4().hex}.pdf",
                content=MINIMAL_PDF_BYTES,
                content_type="application/pdf",
            ),
            patient=self.patient,
            center=self.center_a,
            anonymized_text="scoped text",
        )
        self.video = VideoFile.objects.create(
            center=self.center_a,
            patient=self.patient,
            examination=None,
            video_hash=f"scope-video-{uuid4().hex}",
            original_file_name="scope.mp4",
        )
        self.client.force_login(
            User.objects.create_user(username="center-scope-reader")
        )

    @staticmethod
    def _pk(model: object) -> int:
        return cast(int, cast(Any, model).pk)

    @patch("endoreg_db.views.access_control.resolve_allowed_center_id")
    def test_patient_timeline_is_denied_outside_center_scope(
        self, mock_allowed_center_id: MagicMock
    ) -> None:
        mock_allowed_center_id.return_value = self._pk(self.center_b)

        response = self.client.get(
            f"/api/media/patients/{self._pk(self.patient)}/timeline/"
        )

        assert response.status_code == 404, response.content

    @patch("endoreg_db.views.access_control.resolve_allowed_center_id")
    def test_pdf_detail_is_denied_outside_center_scope(
        self, mock_allowed_center_id: MagicMock
    ) -> None:
        mock_allowed_center_id.return_value = self._pk(self.center_b)

        response = self.client.get(f"/api/media/pdfs/{self._pk(self.report)}/")

        assert response.status_code == 404, response.content

    @patch("endoreg_db.views.access_control.resolve_allowed_center_id")
    def test_pdf_list_is_filtered_by_center_scope(
        self, mock_allowed_center_id: MagicMock
    ) -> None:
        mock_allowed_center_id.return_value = self._pk(self.center_a)
        other_patient = Patient.objects.create(
            first_name="Other",
            last_name="Patient",
            center=self.center_b,
            is_real_person=False,
            patient_hash=f"other-patient-{uuid4().hex}",
        )
        RawPdfFile.objects.create(
            pdf_hash=f"scope-pdf-other-{uuid4().hex}",
            file=SimpleUploadedFile(
                name=f"scope-other-{uuid4().hex}.pdf",
                content=MINIMAL_PDF_BYTES,
                content_type="application/pdf",
            ),
            patient=other_patient,
            center=self.center_b,
            anonymized_text="other scoped text",
        )

        response = self.client.get("/api/media/pdfs/")

        assert response.status_code == 200, response.content
        payload = cast(dict[str, Any], response.json())
        assert payload["count"] == 1

        results = cast(list[dict[str, Any]], payload["results"])
        returned_ids = {cast(int, item["id"]) for item in results}
        assert self._pk(self.report) in returned_ids

    @patch("endoreg_db.views.access_control.resolve_allowed_center_id")
    def test_report_stream_is_denied_outside_center_scope(
        self, mock_allowed_center_id: MagicMock
    ) -> None:
        mock_allowed_center_id.return_value = self._pk(self.center_b)

        response = self.client.get(f"/api/media/pdfs/{self._pk(self.report)}/stream/")

        assert response.status_code == 404

    @patch("endoreg_db.views.access_control.resolve_allowed_center_id")
    def test_video_stream_is_denied_outside_center_scope(
        self, mock_allowed_center_id: MagicMock
    ) -> None:
        mock_allowed_center_id.return_value = self._pk(self.center_b)

        response = self.client.get(f"/api/media/videos/{self._pk(self.video)}/stream/")

        assert response.status_code == 404, response.content

    @patch("endoreg_db.views.access_control.resolve_allowed_center_id")
    def test_video_sensitive_metadata_is_denied_outside_center_scope(
        self, mock_allowed_center_id: MagicMock
    ) -> None:
        mock_allowed_center_id.return_value = self._pk(self.center_b)

        response = self.client.get(
            f"/api/media/videos/{self._pk(self.video)}/sensitive-metadata/"
        )

        assert response.status_code == 404, response.content

from __future__ import annotations

from uuid import uuid4

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from endoreg_db.models import Center, Examiner, PortalUserInfo, RawPdfFile
from endoreg_db.models import ReportLlmInferenceJob

MINIMAL_PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


class ReportLlmStatusScopeTests(TestCase):
    def setUp(self) -> None:
        self.center_a = Center.objects.create(
            name=f"report-llm-center-a-{uuid4().hex[:8]}",
            display_name="Report LLM Center A",
        )
        self.center_b = Center.objects.create(
            name=f"report-llm-center-b-{uuid4().hex[:8]}",
            display_name="Report LLM Center B",
        )
        self.report = RawPdfFile.objects.create(
            center=self.center_a,
            pdf_hash=f"report-llm-status-{uuid4().hex}",
            file=SimpleUploadedFile(
                name=f"report-llm-status-{uuid4().hex}.pdf",
                content=MINIMAL_PDF_BYTES,
                content_type="application/pdf",
            ),
        )
        self.job = ReportLlmInferenceJob.objects.create(
            pdf=self.report,
            operation=ReportLlmInferenceJob.OPERATION_REIMPORT,
            status=ReportLlmInferenceJob.STATUS_QUEUED,
            task_id="report-status-task",
            queue="llm_inference",
            config={"kind": "report_llm_reimport", "queue": "llm_inference"},
        )

    def _login_center_user(self, center: Center) -> None:
        username = f"report-llm-user-{uuid4().hex}"
        user = User.objects.create_user(username=username, password="secret")
        examiner = Examiner.objects.create(
            first_name="Report",
            last_name="Scoped",
            hash=f"report-llm-examiner-{uuid4().hex}",
            center=center,
        )
        PortalUserInfo.objects.create(user=user, examiner=examiner)
        self.client.force_login(user)

    def test_report_reimport_returns_404_for_cross_center_access(self):
        self._login_center_user(self.center_b)

        response = self.client.post(f"/api/media/pdfs/{self.report.pk}/reimport/")

        assert response.status_code == 404, response.content

    def test_report_llm_job_status_returns_404_for_cross_center_access(self):
        self._login_center_user(self.center_b)

        response = self.client.get(
            f"/api/media/pdfs/{self.report.pk}/llm-jobs/{self.job.job_key}/"
        )

        assert response.status_code == 404, response.content

    def test_report_llm_job_status_returns_404_for_mismatched_pdf(self):
        other_report = RawPdfFile.objects.create(
            center=self.center_a,
            pdf_hash=f"report-llm-status-other-{uuid4().hex}",
            file=SimpleUploadedFile(
                name=f"report-llm-status-other-{uuid4().hex}.pdf",
                content=MINIMAL_PDF_BYTES,
                content_type="application/pdf",
            ),
        )
        self._login_center_user(self.center_a)

        response = self.client.get(
            f"/api/media/pdfs/{other_report.pk}/llm-jobs/{self.job.job_key}/"
        )

        assert response.status_code == 404, response.content

    def test_report_llm_job_status_allows_same_center_user(self):
        self._login_center_user(self.center_a)

        response = self.client.get(
            f"/api/media/pdfs/{self.report.pk}/llm-jobs/{self.job.job_key}/"
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["job_id"] == self.job.job_key
        assert payload["report_id"] == self.report.pk
        assert payload["poll_url"] == (
            f"/endoreg-api/media/pdfs/{self.report.pk}/llm-jobs/{self.job.job_key}/"
        )

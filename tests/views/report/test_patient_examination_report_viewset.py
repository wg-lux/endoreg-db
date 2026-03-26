from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase

from endoreg_db.models import (
    Center,
    Examination,
    Frame,
    LabelVideoSegment,
    Patient,
    PatientExamination,
    PatientExaminationReport,
    VideoFile,
)

User = get_user_model()


class PatientExaminationReportViewSetTests(TestCase):
    def test_history_context_requires_patient_examination_id(self):
        resp = self.client.get("/api/patient-examination-reports/history-context/")
        assert resp.status_code == 400
        assert "patient_examination_id" in resp.json()["detail"]

    def test_history_context_rejects_non_integer_patient_examination_id(self):
        resp = self.client.get(
            "/api/patient-examination-reports/history-context/?patient_examination_id=abc"
        )
        assert resp.status_code == 400

    def test_save_submission_returns_history_and_warnings(self):
        from endoreg_db.views.report import patient_examination_report as view_module

        class _FakeSerializer:
            def __init__(self, *args, **kwargs):
                self.validated_data = kwargs.get("data", {})

            def is_valid(self, raise_exception=False):
                return True

        fake_report = SimpleNamespace(
            id=1,
            patient_examination=1,
            template_name="t",
            template_version="",
            template_hash="",
            title="",
            status=PatientExaminationReport.Status.DRAFT,
            editor_payload={},
            patient_context_snapshot={},
            history_context_snapshot={},
            rendered_text="",
            version=1,
            is_active=True,
            created_at=None,
            updated_at=None,
            finalized_at=None,
            created_by=None,
            updated_by=None,
            finalized_by=None,
        )

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module,
            "PatientExaminationReportSubmissionSerializer",
            _FakeSerializer,
        )
        monkeypatches.setattr(
            view_module.PatientExaminationReportViewSet,
            "_get_scoped_patient_examination",
            lambda self, pe_id: object(),
        )
        monkeypatches.setattr(
            view_module,
            "save_report_submission",
            lambda **kwargs: SimpleNamespace(
                report=fake_report,
                created=True,
                warnings=["nag"],
                history_context={"previous_examinations": []},
                persisted_report_artifact_id=None,
                persisted_pdf_artifact_id=None,
            ),
        )
        monkeypatches.setattr(
            view_module.PatientExaminationReportSerializer,
            "to_representation",
            lambda self, instance: {"id": 1, "status": "draft", "version": 1},
        )
        try:
            user = User.objects.create_user(username="report-editor", password="pw")
            self.client.force_login(user)
            resp = self.client.post(
                "/api/patient-examination-reports/save-submission/",
                data=json.dumps(
                    {
                        "patient_examination_id": 123,
                        "template_name": "t",
                        "status": "draft",
                    }
                ),
                content_type="application/json",
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code == 201
        data = resp.json()
        assert data["history_context"] == {"previous_examinations": []}
        assert data["warnings"] == ["nag"]


class PatientExaminationReportSegmentFrameSelectorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="report-selector-staff", password="pw", is_staff=True
        )
        self.client.force_login(self.user)

        self.center = Center.objects.create(name="Selector Center")
        self.patient = Patient.objects.create(
            first_name="Pseudo",
            last_name="Patient",
            center=self.center,
            is_real_person=False,
            patient_hash="selector-test-patient-hash",
        )
        self.examination = Examination.objects.create(name="selector_exam")
        self.patient_examination = PatientExamination.objects.create(
            patient=self.patient,
            examination=self.examination,
            date_start="2026-02-23",
            hash="selector-pe-hash",
        )
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="selector-video-hash",
            examination=self.patient_examination,
            patient=self.patient,
            fps=25.0,
            frame_count=100,
            original_file_name="selector.mp4",
        )
        self.segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            start_frame_number=10,
            end_frame_number=20,
        )
        for n in [10, 12, 15, 17, 20]:
            Frame.objects.create(
                video=self.video,
                frame_number=n,
                relative_path=f"frame_{n:04d}.jpg",
                timestamp=n / 25.0,
                is_extracted=True,
            )

    def _selector_url(self, *, report_id: int | None = None) -> str:
        base = (
            f"/api/patient-examination-reports/segment-frame-selector/"
            f"?patient_examination_id={self.patient_examination.id}"
        )
        if report_id is not None:
            base += f"&report_id={report_id}"
        return base

    def _get_segment_item(self, payload: dict) -> dict:
        items = payload.get("results", [])
        assert items, "Expected at least one segment in selector response"
        return next(item for item in items if item["segment_id"] == self.segment.id)

    def test_segment_frame_selector_get_auto_creates_draft_report(self):
        resp = self.client.get(self._selector_url())
        assert resp.status_code == 200, resp.content
        data = resp.json()

        assert data["patient_examination_id"] == self.patient_examination.id
        assert data["auto_created_report"] is True
        assert data["storage_key"] == "report_segment_frame_selections"
        assert data["count"] >= 1

        report_id = data["report_id"]
        report = PatientExaminationReport.objects.get(pk=report_id)
        assert report.status == PatientExaminationReport.Status.DRAFT
        assert report.template_name == "segment_frame_selection"
        assert "report_segment_frame_selections" in (report.editor_payload or {})

        item = self._get_segment_item(data)
        assert item["segment_id"] == self.segment.id
        assert (
            item["controls"]["step_backward_5_frame_number"]
            >= self.segment.start_frame_number
        )
        assert (
            item["controls"]["step_forward_5_frame_number"]
            <= self.segment.end_frame_number
        )

    def test_segment_frame_selector_patch_random_step_set(self):
        first = self.client.get(self._selector_url())
        assert first.status_code == 200, first.content
        report_id = first.json()["report_id"]

        # random
        resp_random = self.client.patch(
            self._selector_url(report_id=report_id),
            data=json.dumps(
                {
                    "patient_examination_id": self.patient_examination.id,
                    "report_id": report_id,
                    "segment_id": self.segment.id,
                    "action": "random",
                }
            ),
            content_type="application/json",
        )
        assert resp_random.status_code == 200, resp_random.content
        random_item = self._get_segment_item(resp_random.json())
        random_selected = random_item["selected_frame_number"]
        assert (
            self.segment.start_frame_number
            <= random_selected
            <= self.segment.end_frame_number
        )

        # step +5
        resp_step = self.client.patch(
            self._selector_url(report_id=report_id),
            data=json.dumps(
                {
                    "patient_examination_id": self.patient_examination.id,
                    "report_id": report_id,
                    "segment_id": self.segment.id,
                    "action": "step",
                    "step": 5,
                }
            ),
            content_type="application/json",
        )
        assert resp_step.status_code == 200, resp_step.content
        step_item = self._get_segment_item(resp_step.json())
        step_selected = step_item["selected_frame_number"]
        assert step_selected == min(self.segment.end_frame_number, random_selected + 5)

        # explicit set
        resp_set = self.client.patch(
            self._selector_url(report_id=report_id),
            data=json.dumps(
                {
                    "patient_examination_id": self.patient_examination.id,
                    "report_id": report_id,
                    "segment_id": self.segment.id,
                    "action": "set",
                    "frame_number": 17,
                }
            ),
            content_type="application/json",
        )
        assert resp_set.status_code == 200, resp_set.content
        set_item = self._get_segment_item(resp_set.json())
        assert set_item["selected_frame_number"] == 17
        assert set_item["selected_frame"]["frame_number"] == 17

        report = PatientExaminationReport.objects.get(pk=report_id)
        stored = (report.editor_payload or {}).get(
            "report_segment_frame_selections", {}
        )
        assert str(self.segment.id) in stored
        assert stored[str(self.segment.id)]["frame_number"] == 17


class PatientExaminationReportCreateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="report-create-staff",
            password="pw",
            is_staff=True,
        )
        self.client.force_login(self.user)

        self.center = Center.objects.create(name="Create Report Center")
        self.patient = Patient.objects.create(
            first_name="Create",
            last_name="Patient",
            center=self.center,
            is_real_person=False,
            patient_hash="create-report-patient-hash",
        )
        self.examination = Examination.objects.create(name="create_report_exam")
        self.patient_examination = PatientExamination.objects.create(
            patient=self.patient,
            examination=self.examination,
            date_start="2026-02-24",
            hash="create-report-pe-hash",
        )

    def test_create_report_minimal_payload(self):
        resp = self.client.post(
            "/api/patient-examination-reports/",
            data=json.dumps(
                {
                    "patient_examination": self.patient_examination.id,
                    "template_name": "star_upper_gi_main",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 201, resp.content
        data = resp.json()

        report = PatientExaminationReport.objects.get(pk=data["id"])
        assert report.patient_examination_id == self.patient_examination.id
        assert report.template_name == "star_upper_gi_main"
        assert report.status == PatientExaminationReport.Status.DRAFT
        assert report.version == 1
        assert report.is_active is True

    def test_create_report_with_payload_and_final_status(self):
        resp = self.client.post(
            "/api/patient-examination-reports/",
            data=json.dumps(
                {
                    "patient_examination": self.patient_examination.id,
                    "template_name": "star_upper_gi_main",
                    "title": "Initial Finalized Draft",
                    "status": "final",
                    "editor_payload": {"sections": [{"id": "findings"}]},
                    "rendered_text": "Rendered report text",
                    "patient_context_snapshot": {"patient_gender": "male"},
                    "history_context_snapshot": {"previous_examinations": []},
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 201, resp.content
        data = resp.json()

        report = PatientExaminationReport.objects.get(pk=data["id"])
        assert report.status == PatientExaminationReport.Status.FINAL
        assert report.title == "Initial Finalized Draft"
        assert report.editor_payload == {"sections": [{"id": "findings"}]}
        assert report.rendered_text == "Rendered report text"
        assert report.patient_context_snapshot == {"patient_gender": "male"}
        assert report.history_context_snapshot == {"previous_examinations": []}


@pytest.mark.django_db
@pytest.mark.xfail(
    reason="Requires stable center-scoped user fixtures and report objects"
)
def test_report_list_scoping_for_non_privileged_user_scaffold():
    """
    Scaffold:
    - create two centers
    - create user linked via portaluserinfo.examiner.center to center A
    - create reports for exams in center A and center B
    - assert list endpoint only returns center A reports (or none without explicit filter)
    """
    raise NotImplementedError

from __future__ import annotations

# pyright: reportUnknownMemberType=false

from datetime import date
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User
from django.test import TestCase

from endoreg_db.models import (
    Center,
    Examination,
    Finding,
    FindingIntervention,
    Frame,
    Label,
    LabelVideoSegment,
    Patient,
    PatientExamination,
    PatientFinding,
    PatientFindingIntervention,
    PortalUserInfo,
    RawPdfFile,
    VideoFile,
)

MINIMAL_PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


class PatientMediaTimelineViewTests(TestCase):
    def setUp(self):
        self.center = Center.objects.create(name=f"timeline-center-{uuid4().hex[:8]}")
        self.examination = Examination.objects.create(
            name=f"timeline-exam-{uuid4().hex[:8]}"
        )
        self.patient = Patient.objects.create(
            first_name="Frontend",
            last_name="Patient",
            center=self.center,
            is_real_person=False,
            patient_hash=f"timeline-patient-{uuid4().hex}",
        )
        self.patient_examination = PatientExamination.objects.create(
            patient=self.patient,
            examination=self.examination,
            hash=f"timeline-pe-{uuid4().hex}",
        )
        user = User.objects.create_user(username=f"timeline-user-{uuid4().hex}")
        portal_info = PortalUserInfo.objects.create(user=user)
        portal_info.centers.add(self.center)
        self.client.force_login(user)

    def _create_report(self, anonymized_text: str) -> RawPdfFile:
        return RawPdfFile.objects.create(
            pdf_hash=f"timeline-pdf-{uuid4().hex}",
            file=SimpleUploadedFile(
                name=f"timeline-{uuid4().hex}.pdf",
                content=MINIMAL_PDF_BYTES,
                content_type="application/pdf",
            ),
            patient=self.patient,
            examination=self.patient_examination,
            anonymized_text=anonymized_text,
        )

    def _create_video(self) -> VideoFile:
        return VideoFile.objects.create(
            center=self.center,
            video_hash=f"timeline-video-{uuid4().hex}",
            examination=self.patient_examination,
            patient=self.patient,
            fps=25.0,
            frame_count=500,
            original_file_name="timeline_video.mp4",
        )

    def test_latest_only_contract_for_reporting_page_with_prioritized_segments(self):
        report = self._create_report(anonymized_text="ANONYMIZED REPORT TEXT")
        video = self._create_video()

        label_polyp = Label.objects.create(name=f"polyp_{uuid4().hex[:6]}")
        label_generic = Label.objects.create(name=f"generic_{uuid4().hex[:6]}")
        label_generic_other = Label.objects.create(name=f"generic_{uuid4().hex[:6]}_2")

        segment_polyp = LabelVideoSegment.objects.create(
            video_file=video,
            label=label_polyp,
            start_frame_number=100,
            end_frame_number=110,
        )
        segment_intervention = LabelVideoSegment.objects.create(
            video_file=video,
            label=label_generic,
            start_frame_number=200,
            end_frame_number=210,
        )
        segment_other = LabelVideoSegment.objects.create(
            video_file=video,
            label=label_generic_other,
            start_frame_number=300,
            end_frame_number=305,
        )

        finding_intervention = Finding.objects.create(name=f"finding-{uuid4().hex[:8]}")
        finding_other = Finding.objects.create(name=f"finding-{uuid4().hex[:8]}-other")
        self.examination.findings.add(finding_intervention, finding_other)

        pf_intervention = PatientFinding.objects.create(
            patient_examination=self.patient_examination,
            finding=finding_intervention,
        )
        pf_other = PatientFinding.objects.create(
            patient_examination=self.patient_examination,
            finding=finding_other,
        )

        intervention = FindingIntervention.objects.create(
            name=f"intervention-{uuid4().hex[:8]}"
        )
        PatientFindingIntervention.objects.create(
            finding=pf_intervention,
            intervention=intervention,
            is_active=True,
        )

        segment_intervention.patient_findings.add(pf_intervention)
        segment_other.patient_findings.add(pf_other)

        response = self.client.get(
            (
                f"/api/media/patients/{self.patient.pk}/timeline/"
                f"?patient_examination_id={self.patient_examination.pk}&latest_only=true"
            )
        )

        assert response.status_code == 200, response.content
        payload = response.json()

        latest_report = payload["latest_report"]
        assert latest_report is not None
        assert latest_report["id"] == report.pk
        assert latest_report["anonymized_text"] == "ANONYMIZED REPORT TEXT"
        assert [entry["type"] for entry in latest_report["stream_options"]] == ["raw"]

        latest_video = payload["latest_video"]
        assert latest_video is not None
        assert latest_video["id"] == video.pk
        assert latest_video["stream_options"] == [
            {
                "type": "processed",
                "url": (
                    "http://testserver/endoreg-api/media/videos/"
                    f"{video.pk}/hls/playlist.m3u8?type=processed"
                ),
            }
        ]

        latest_frames = payload["latest_frames"]
        assert len(latest_frames) == 3
        assert [item["category"] for item in latest_frames] == [
            "polyp",
            "intervention",
            "other_findings",
        ]
        assert [item["frame_number"] for item in latest_frames] == [109, 209, 304]
        assert [item["segment_id"] for item in latest_frames] == [
            segment_polyp.pk,
            segment_intervention.pk,
            segment_other.pk,
        ]
        for frame_item in latest_frames:
            expected_path = (
                f"/endoreg-api/media/videos/{video.pk}/frames/"
                f"{frame_item['frame_number']}/stream/"
            )
            assert frame_item["stream_url"].endswith(expected_path)
            assert frame_item["selection_source"] == "segment_priority"

    def test_latest_only_full_report_exposes_raw_pdf_id_for_frontend_streams(self):
        from endoreg_db.models import AnonymExaminationReport

        raw_pdf = self._create_report(anonymized_text="")
        full_report = AnonymExaminationReport.objects.create(
            patient=self.patient,
            patient_examination=self.patient_examination,
            text="FULL REPORT TEXT",
            date=date(2099, 1, 1),
        )
        raw_pdf.anonym_examination_report = full_report
        raw_pdf.save(update_fields=["anonym_examination_report"])

        response = self.client.get(
            (
                f"/api/media/patients/{self.patient.pk}/timeline/"
                f"?patient_examination_id={self.patient_examination.pk}&latest_only=true"
            )
        )

        assert response.status_code == 200, response.content
        latest_report = response.json()["latest_report"]
        assert latest_report["media_type"] == "full_report"
        assert latest_report["id"] == full_report.pk
        assert latest_report["raw_pdf_id"] == raw_pdf.pk
        assert latest_report["anonymized_text"] == "FULL REPORT TEXT"
        assert [entry["type"] for entry in latest_report["stream_options"]] == ["raw"]
        assert all(
            f"/endoreg-api/media/pdfs/{raw_pdf.pk}/stream/" in option["url"]
            for option in latest_report["stream_options"]
        )

    def test_latest_only_falls_back_to_latest_frame_rows_when_no_segments(self):
        self._create_report(anonymized_text="TEXT")
        video = self._create_video()

        Frame.objects.create(
            video=video,
            frame_number=5,
            relative_path="frame_0000005.jpg",
            timestamp=0.2,
            is_extracted=True,
        )
        Frame.objects.create(
            video=video,
            frame_number=9,
            relative_path="frame_0000009.jpg",
            timestamp=0.36,
            is_extracted=True,
        )
        Frame.objects.create(
            video=video,
            frame_number=7,
            relative_path="frame_0000007.jpg",
            timestamp=0.28,
            is_extracted=True,
        )

        response = self.client.get(
            f"/api/media/patients/{self.patient.pk}/timeline/?latest_only=true"
        )
        assert response.status_code == 200, response.content
        payload = response.json()

        latest_frames = payload["latest_frames"]
        assert len(latest_frames) == 3
        assert [item["frame_number"] for item in latest_frames] == [9, 7, 5]
        assert all(item["category"] == "fallback_latest" for item in latest_frames)
        assert all(item["selection_source"] == "latest_frame" for item in latest_frames)
        for frame_item in latest_frames:
            expected_path = (
                f"/endoreg-api/media/videos/{video.pk}/frames/"
                f"{frame_item['frame_number']}/stream/"
            )
            assert frame_item["stream_url"].endswith(expected_path)

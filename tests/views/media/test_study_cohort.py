from __future__ import annotations

import hashlib
from datetime import date
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from endoreg_db.models import (
    Center,
    Examination,
    Finding,
    Frame,
    ImageClassificationAnnotation,
    Label,
    Patient,
    PatientExamination,
    PatientFinding,
    RawPdfFile,
    RawPdfState,
    VideoFile,
    VideoState,
)


PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
VIDEO_BYTES = b"anonymized-video-study-fixture"


class StudyCohortPreviewViewTests(TestCase):
    def setUp(self) -> None:
        suffix = uuid4().hex[:10]
        self.center = Center.objects.create(
            name=f"study-center-{suffix}",
            display_name="Register Center",
        )
        self.examination = Examination.objects.create(name=f"colonoscopy-{suffix}")
        self.patient = Patient.objects.create(
            first_name="Pseudonym",
            last_name="Only",
            dob=date(1970, 1, 1),
            center=self.center,
            is_real_person=False,
            patient_hash=f"patient-hash-{uuid4().hex}",
        )
        self.patient_examination = PatientExamination.objects.create(
            patient=self.patient,
            examination=self.examination,
            date_start=date(2026, 6, 1),
            hash=f"case-hash-{uuid4().hex}",
        )

    def _validated_report(self, *, raw_text: str = "SENTINEL RAW PHI") -> RawPdfFile:
        state = RawPdfState.objects.create(
            anonymized=True,
            anonymization_validated=True,
            sensitive_meta_processed=True,
            processed_file_sha256=hashlib.sha256(PDF_BYTES).hexdigest(),
        )
        return RawPdfFile.objects.create(
            pdf_hash=hashlib.sha256(f"raw-{uuid4().hex}".encode()).hexdigest(),
            file=SimpleUploadedFile(
                f"raw-{uuid4().hex}.pdf",
                PDF_BYTES,
                content_type="application/pdf",
            ),
            processed_file=SimpleUploadedFile(
                f"processed-{uuid4().hex}.pdf",
                PDF_BYTES,
                content_type="application/pdf",
            ),
            state=state,
            patient=self.patient,
            examination=self.patient_examination,
            center=self.center,
            text=raw_text,
            anonymized_text="No identifying content",
            raw_meta={"document_type": "endoscopy-report"},
        )

    def _validated_video(self) -> VideoFile:
        state = VideoState.objects.create(
            anonymized=True,
            anonymization_validated=True,
            sensitive_meta_processed=True,
            processed_file_sha256=hashlib.sha256(VIDEO_BYTES).hexdigest(),
        )
        return VideoFile.objects.create(
            center=self.center,
            video_hash=hashlib.sha256(f"video-{uuid4().hex}".encode()).hexdigest(),
            processed_video_hash=hashlib.sha256(VIDEO_BYTES).hexdigest(),
            processed_file=SimpleUploadedFile(
                f"processed-{uuid4().hex}.mp4",
                VIDEO_BYTES,
                content_type="video/mp4",
            ),
            state=state,
            patient=self.patient,
            examination=self.patient_examination,
            original_file_name="SENTINEL-PATIENT-NAME.mp4",
        )

    def test_groups_only_validated_processed_report_and_video_by_pseudonymous_case(
        self,
    ) -> None:
        report = self._validated_report()
        video = self._validated_video()
        finding = Finding.objects.create(name=f"adenoma-{uuid4().hex[:8]}")
        PatientFinding.objects.create(
            patient_examination=self.patient_examination,
            finding=finding,
        )
        label = Label.objects.create(name=f"polyp-{uuid4().hex[:8]}")
        frame = Frame.objects.create(
            video=video,
            frame_number=42,
            relative_path="frame_0000042.jpg",
            timestamp=1.68,
        )
        ImageClassificationAnnotation.objects.create(
            frame=frame,
            label=label,
            value=True,
        )

        response = self.client.get(
            "/api/media/studies/cohort-preview/",
            {
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
                "center_key": self.center.center_key,
                "examination_name": self.examination.name,
                "document_type": "endoscopy-report",
                "finding": finding.name,
                "annotation_label": label.name,
                "has_report": "true",
                "has_video": "true",
            },
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["schema_version"] == "1.0"
        assert payload["summary"] == {
            "case_count": 1,
            "patient_count": 1,
            "report_count": 1,
            "video_count": 1,
        }
        assert len(payload["cases"]) == 1
        case = payload["cases"][0]
        assert case["patient_examination_id"] == self.patient_examination.pk
        assert case["case_hash"] == self.patient_examination.hash
        assert case["patient_hash"] == self.patient.patient_hash
        assert case["center_keys"] == [self.center.center_key]
        assert case["findings"] == [finding.name]
        assert case["annotation_labels"] == [label.name]
        assert case["reports"] == [
            {
                "id": report.pk,
                "document_type": "endoscopy-report",
                "stream_url": (
                    "http://testserver/endoreg-api/media/pdfs/"
                    f"{report.pk}/stream/?type=processed"
                ),
                "availability": "local",
            }
        ]
        assert case["videos"] == [
            {
                "id": video.pk,
                "stream_url": (
                    "http://testserver/endoreg-api/media/videos/"
                    f"{video.pk}/hls/playlist.m3u8?type=processed"
                ),
                "availability": "local",
            }
        ]
        serialized = response.content.decode("utf-8")
        assert "SENTINEL RAW PHI" not in serialized
        assert "SENTINEL-PATIENT-NAME.mp4" not in serialized
        assert self.patient.first_name not in serialized
        assert self.patient.last_name not in serialized
        assert str(self.patient.dob) not in serialized

    def test_excludes_real_patients_unvalidated_media_and_missing_integrity_hashes(
        self,
    ) -> None:
        unvalidated_state = RawPdfState.objects.create(
            anonymized=True,
            anonymization_validated=False,
            processed_file_sha256=hashlib.sha256(PDF_BYTES).hexdigest(),
        )
        RawPdfFile.objects.create(
            pdf_hash=hashlib.sha256(f"unvalidated-{uuid4().hex}".encode()).hexdigest(),
            processed_file=SimpleUploadedFile(
                f"unvalidated-{uuid4().hex}.pdf",
                PDF_BYTES,
                content_type="application/pdf",
            ),
            state=unvalidated_state,
            patient=self.patient,
            examination=self.patient_examination,
            center=self.center,
        )
        missing_hash_state = VideoState.objects.create(
            anonymized=True,
            anonymization_validated=True,
            processed_file_sha256="",
        )
        VideoFile.objects.create(
            center=self.center,
            video_hash=hashlib.sha256(f"no-hash-{uuid4().hex}".encode()).hexdigest(),
            processed_file=SimpleUploadedFile(
                f"no-hash-{uuid4().hex}.mp4",
                VIDEO_BYTES,
                content_type="video/mp4",
            ),
            state=missing_hash_state,
            patient=self.patient,
            examination=self.patient_examination,
        )

        real_patient = Patient.objects.create(
            first_name="Real",
            last_name="Person",
            center=self.center,
            is_real_person=True,
            patient_hash=f"real-{uuid4().hex}",
        )
        real_case = PatientExamination.objects.create(
            patient=real_patient,
            examination=self.examination,
            hash=f"real-case-{uuid4().hex}",
        )
        state = VideoState.objects.create(
            anonymized=True,
            anonymization_validated=True,
            processed_file_sha256=hashlib.sha256(VIDEO_BYTES).hexdigest(),
        )
        VideoFile.objects.create(
            center=self.center,
            video_hash=hashlib.sha256(f"real-video-{uuid4().hex}".encode()).hexdigest(),
            processed_file=SimpleUploadedFile(
                f"real-{uuid4().hex}.mp4",
                VIDEO_BYTES,
                content_type="video/mp4",
            ),
            state=state,
            patient=real_patient,
            examination=real_case,
        )

        response = self.client.get("/api/media/studies/cohort-preview/")

        assert response.status_code == 200, response.content
        assert response.json()["summary"] == {
            "case_count": 0,
            "patient_count": 0,
            "report_count": 0,
            "video_count": 0,
        }
        assert response.json()["cases"] == []

    def test_rejects_ambiguous_filters_and_unbounded_preview(self) -> None:
        invalid_bool = self.client.get(
            "/api/media/studies/cohort-preview/", {"has_report": "sometimes"}
        )
        assert invalid_bool.status_code == 400
        assert invalid_bool.json()["error"] == "has_report must be true or false."

        invalid_dates = self.client.get(
            "/api/media/studies/cohort-preview/",
            {"date_from": "2026-12-31", "date_to": "2026-01-01"},
        )
        assert invalid_dates.status_code == 400

        invalid_limit = self.client.get(
            "/api/media/studies/cohort-preview/", {"limit": "501"}
        )
        assert invalid_limit.status_code == 400

from __future__ import annotations

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from collections.abc import Callable
from datetime import date, datetime, time
from uuid import uuid4
import json
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http.response import HttpResponseBase
from django.test import Client as DjangoClient
from django.utils import timezone
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response as DRFResponse
from rest_framework.test import APIRequestFactory, force_authenticate
from django.contrib.auth.models import User

from endoreg_db.models import (
    AnonymExaminationReport,
    Center,
    Examination,
    Gender,
    Patient,
    PatientExamination,
    PortalUserInfo,
    RawPdfFile,
    SensitiveMeta,
    VideoFile,
    Tag,
)
from endoreg_db.models.state.audit_ledger import AuditLedger, LedgerHead
from endoreg_db.views.anonymization.validate import AnonymizationValidateView

MINIMAL_PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.mark.django_db
class TestSensitiveMetadataEndpoints:
    @pytest.fixture
    def factory(self) -> APIRequestFactory:
        return APIRequestFactory()

    @pytest.fixture
    def user(self) -> User:
        return User.objects.create_user(username=f"sm-user-{uuid4().hex[:8]}")

    @pytest.fixture(autouse=True)
    def authenticate_center_user(
        self,
        client: DjangoClient,
        user: User,
        sensitive_meta: SensitiveMeta,
    ) -> None:
        portal_info = PortalUserInfo.objects.create(user=user)
        assert sensitive_meta.center is not None
        portal_info.centers.add(sensitive_meta.center)
        client.force_login(user)

    @pytest.fixture
    def sensitive_meta(self) -> SensitiveMeta:
        suffix = uuid4().hex[:8]
        patient = Patient.objects.create(
            first_name="Pseudo",
            last_name="Patient",
            patient_hash=f"sm-patient-{suffix}",
        )
        examination = PatientExamination.objects.create(patient=patient)
        gender = Gender.objects.create(name=f"gender-{suffix}")
        center = Center.objects.create(name=f"center-{suffix}")
        return SensitiveMeta.objects.create(
            patient_first_name="Max",
            patient_last_name="Mustermann",
            patient_dob=timezone.make_aware(datetime(1994, 3, 21, 0, 0)),
            examination_date=date(2025, 11, 27),
            examination_time=time(9, 30),
            casenumber=f"CASE-{suffix}",
            file_path="/tmp/some/file.pdf",
            pseudo_patient=patient,
            pseudo_examination=examination,
            patient_gender=gender,
            center=center,
            examiner_first_name="Dr.",
            examiner_last_name="Examiner",
        )

    @pytest.fixture
    def video(self, sensitive_meta: SensitiveMeta) -> VideoFile:
        center = sensitive_meta.center
        assert center is not None
        return VideoFile.objects.create(
            center=center,
            sensitive_meta=sensitive_meta,
            video_hash=f"video-sm-{uuid4().hex}",
            original_file_name="sm-video.mp4",
        )

    @pytest.fixture
    def pdf(self, sensitive_meta: SensitiveMeta) -> RawPdfFile:
        return RawPdfFile.objects.create(
            pdf_hash=f"pdf-sm-{uuid4().hex}",
            file=SimpleUploadedFile(
                name=f"sm-{uuid4().hex}.pdf",
                content=MINIMAL_PDF_BYTES,
                content_type="application/pdf",
            ),
            sensitive_meta=sensitive_meta,
            raw_meta={"document_type": "report_draft"},
        )

    def _call_view(
        self,
        view: Callable[..., HttpResponseBase],
        request: Request,
        **kwargs: object,
    ) -> DRFResponse:
        response = view(request, **kwargs)
        assert isinstance(response, DRFResponse)
        return response

    def _require_sensitive_meta(
        self, sensitive_meta: SensitiveMeta | None
    ) -> SensitiveMeta:
        assert sensitive_meta is not None
        return sensitive_meta

    def _require_anonym_report(
        self, report: AnonymExaminationReport | None
    ) -> AnonymExaminationReport:
        assert report is not None
        return report

    def _require_report_type_name(self, report: AnonymExaminationReport) -> str:
        report_type = report.type
        assert report_type is not None
        return str(report_type.name)

    def test_get_video_sensitive_metadata_success(
        self, client: DjangoClient, video: VideoFile
    ) -> None:
        response = client.get(f"/api/media/videos/{video.pk}/sensitive-metadata/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["patient_first_name"] == "Max"
        assert payload["patient_last_name"] == "Mustermann"
        assert (
            payload["pseudo_patient_id"]
            == self._require_sensitive_meta(video.sensitive_meta).pseudo_patient_id
        )
        assert (
            payload["pseudo_examination_id"]
            == self._require_sensitive_meta(video.sensitive_meta).pseudo_examination_id
        )
        assert payload["patient_hash_display"].startswith("...")
        assert payload["examination_hash_display"].startswith("...")

    def test_get_video_sensitive_metadata_includes_tags_and_validation_comment(
        self, client: DjangoClient, video: VideoFile
    ) -> None:
        assert video.sensitive_meta is not None
        review_tag = Tag.objects.create(name="Nochmal Überprüfen")
        excluded_tag = Tag.objects.create(name="Ausgeschlossen")
        self._require_sensitive_meta(video.sensitive_meta).tags.set(
            [review_tag, excluded_tag]
        )
        self._require_sensitive_meta(
            video.sensitive_meta
        ).validation_comment = "Freitext zur Nachkontrolle"
        self._require_sensitive_meta(video.sensitive_meta).save(
            update_fields=["validation_comment"]
        )

        response = client.get(f"/api/media/videos/{video.pk}/sensitive-metadata/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["validation_comment"] == "Freitext zur Nachkontrolle"
        assert sorted(payload["tags"]) == ["Ausgeschlossen", "Nochmal Überprüfen"]

    def test_patch_video_sensitive_metadata(
        self, client: DjangoClient, video: VideoFile
    ) -> None:
        response = client.patch(
            f"/api/media/videos/{video.pk}/sensitive-metadata/",
            data={"patient_first_name": "Anna"},
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["video_id"] == video.pk
        assert payload["sensitive_meta"]["patient_first_name"] == "Anna"

    def test_verify_video_sensitive_metadata(
        self, client: DjangoClient, video: VideoFile
    ) -> None:
        response = client.post(
            f"/api/media/videos/{video.pk}/sensitive-metadata/verify/",
            data={"dob_verified": True, "names_verified": False},
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["video_id"] == video.pk
        assert payload["state_verified"] in (True, False)

    def test_get_pdf_sensitive_metadata_success(
        self, client: DjangoClient, pdf: RawPdfFile
    ) -> None:
        response = client.get(f"/api/media/pdfs/{pdf.pk}/sensitive-metadata/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["patient_first_name"] == "Max"
        assert payload["patient_last_name"] == "Mustermann"
        assert (
            payload["pseudo_patient_id"]
            == self._require_sensitive_meta(pdf.sensitive_meta).pseudo_patient_id
        )
        assert (
            payload["pseudo_examination_id"]
            == self._require_sensitive_meta(pdf.sensitive_meta).pseudo_examination_id
        )

    def test_get_video_case_resolution_success(
        self, client: DjangoClient, video: VideoFile
    ) -> None:
        response = client.get(f"/api/media/videos/{video.pk}/case-resolution/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["media_type"] == "video"
        assert payload["media_id"] == video.pk
        assert payload["sensitive_meta_id"] == video.sensitive_meta_id
        assert (
            payload["pseudo_patient"]["id"]
            == self._require_sensitive_meta(video.sensitive_meta).pseudo_patient_id
        )
        assert (
            payload["pseudo_examination"]["id"]
            == self._require_sensitive_meta(video.sensitive_meta).pseudo_examination_id
        )
        assert payload["match_status"] == "suggested"
        assert payload["is_explicitly_resolved"] is False
        assert payload["linked_patient_examination_id"] is None
        assert (
            payload["recommended_patient_examination_id"]
            == self._require_sensitive_meta(video.sensitive_meta).pseudo_examination_id
        )

    def test_get_pdf_case_resolution_success(
        self, client: DjangoClient, pdf: RawPdfFile
    ) -> None:
        response = client.get(f"/api/media/pdfs/{pdf.pk}/case-resolution/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["media_type"] == "pdf"
        assert payload["media_id"] == pdf.pk
        assert payload["sensitive_meta_id"] == pdf.sensitive_meta_id
        assert payload["match_status"] == "linked"
        assert payload["is_explicitly_resolved"] is False
        assert payload["linked_patient_examination_id"] == pdf.examination_id
        assert (
            payload["pseudo_examination"]["linked_patient_examination_id"]
            == pdf.examination_id
        )
        assert (
            payload["patient_examination_matches"][0]["id"]
            == self._require_sensitive_meta(pdf.sensitive_meta).pseudo_examination_id
        )

    def test_get_pdf_case_resolution_unresolved_when_no_hash_matches(
        self, client: DjangoClient, pdf: RawPdfFile
    ) -> None:
        SensitiveMeta.objects.filter(pk=pdf.sensitive_meta_id).update(
            patient_hash=f"no-patient-match-{uuid4().hex}",
            examination_hash=f"no-examination-match-{uuid4().hex}",
        )
        pdf.refresh_from_db()
        self._require_sensitive_meta(pdf.sensitive_meta).refresh_from_db()

        response = client.get(f"/api/media/pdfs/{pdf.pk}/case-resolution/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["match_status"] == "linked"
        assert payload["linked_patient_examination_id"] == pdf.examination_id

    def test_validate_video_auto_links_exact_case_match_and_updates_read_side(
        self,
        client: DjangoClient,
        factory: APIRequestFactory,
        user: User,
        video: VideoFile,
    ) -> None:
        validation_payload = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "file_type": "video",
        }

        validation_request = factory.post(
            f"/api/anonymization/{video.pk}/validate/",
            data=validation_payload,
            format="json",
        )
        force_authenticate(validation_request, user=user)
        validation_view = AnonymizationValidateView.as_view()
        response = self._call_view(
            validation_view, validation_request, file_id=video.pk
        )

        data = json.loads(response.content)

        assert response.status_code == status.HTTP_200_OK

        video.refresh_from_db()
        assert (
            video.examination_id
            == self._require_sensitive_meta(video.sensitive_meta).pseudo_examination_id
        )
        assert (
            video.patient_id
            == self._require_sensitive_meta(video.sensitive_meta).pseudo_patient_id
        )
        assert data["case_resolution"]["status"] == "linked"
        assert (
            data["case_resolution"]["patient_examination_id"]
            == self._require_sensitive_meta(video.sensitive_meta).pseudo_examination_id
        )
        assert data["case_resolution"]["created"] is False
        assert data["case_resolution"]["reason"] in {
            "matched_by_hash",
            "already_linked",
        }

        read_response = client.get(f"/api/media/videos/{video.pk}/case-resolution/")

        assert read_response.status_code == 200, read_response.content
        read_payload = read_response.json()
        assert read_payload["match_status"] == "linked"
        assert read_payload["is_explicitly_resolved"] is False
        assert read_payload["is_auto_resolved"] is True
        assert (
            read_payload["linked_patient_examination_id"]
            == self._require_sensitive_meta(video.sensitive_meta).pseudo_examination_id
        )
        assert (
            read_payload["pseudo_examination"]["linked_patient_examination_id"]
            == self._require_sensitive_meta(video.sensitive_meta).pseudo_examination_id
        )

    def test_post_video_case_resolution_attach_existing(
        self, client: DjangoClient, video: VideoFile
    ) -> None:
        target_patient = Patient.objects.create(
            first_name="Attach",
            last_name="Target",
            patient_hash=f"attach-patient-{uuid4().hex[:8]}",
        )
        target_patient_examination = PatientExamination.objects.create(
            patient=target_patient
        )

        response = client.post(
            f"/api/media/videos/{video.pk}/case-resolution/",
            data={
                "action": "attach",
                "patient_examination_id": target_patient_examination.pk,
            },
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        video.refresh_from_db()
        target_patient_examination.refresh_from_db()

        assert payload["action"] == "attach"
        assert payload["status"] == "linked"
        assert payload["created"] is False
        assert payload["patient_examination_id"] == target_patient_examination.pk
        assert payload["patient_id"] == target_patient.pk
        assert (
            payload["case_resolution"]["pseudo_examination"][
                "linked_patient_examination_id"
            ]
            == target_patient_examination.pk
        )
        assert payload["case_resolution"]["match_status"] == "linked"
        assert payload["case_resolution"]["is_explicitly_resolved"] is True
        assert video.examination_id == target_patient_examination.pk
        assert video.patient_id == target_patient.pk
        assert target_patient_examination.video_id == video.pk

    def test_post_pdf_case_resolution_create_new(
        self, client: DjangoClient, pdf: RawPdfFile
    ) -> None:
        examination = Examination.objects.create(name=f"colonoscopy-{uuid4().hex[:8]}")
        pdf.anonymized_text = "Latest corrected text for explicit create"
        pdf.save(update_fields=["anonymized_text"])

        response = client.post(
            f"/api/media/pdfs/{pdf.pk}/case-resolution/",
            data={
                "action": "create",
                "patient_id": self._require_sensitive_meta(
                    pdf.sensitive_meta
                ).pseudo_patient_id,
                "examination_name": examination.name,
                "date_start": "2025-11-28",
            },
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        pdf.refresh_from_db()
        created_patient_examination = PatientExamination.objects.get(
            pk=payload["patient_examination_id"]
        )

        assert payload["action"] == "create"
        assert payload["status"] == "linked"
        assert payload["created"] is True
        assert (
            payload["patient_id"]
            == self._require_sensitive_meta(pdf.sensitive_meta).pseudo_patient_id
        )
        assert (
            created_patient_examination.patient_id
            == self._require_sensitive_meta(pdf.sensitive_meta).pseudo_patient_id
        )
        assert created_patient_examination.examination_id == examination.pk
        assert str(created_patient_examination.date_start) == "2025-11-28"
        assert pdf.examination_id == created_patient_examination.pk
        assert (
            pdf.patient_id
            == self._require_sensitive_meta(pdf.sensitive_meta).pseudo_patient_id
        )
        assert pdf.anonym_examination_report_id is not None
        assert (
            self._require_report_type_name(
                self._require_anonym_report(pdf.anonym_examination_report)
            )
            == "report_draft"
        )
        assert (
            self._require_anonym_report(pdf.anonym_examination_report).text
            == "Latest corrected text for explicit create"
        )
        assert payload["case_resolution"]["match_status"] == "linked"
        assert payload["case_resolution"]["is_explicitly_resolved"] is True

    def test_post_pdf_case_resolution_create_new_patient_and_examination(
        self, client: DjangoClient, pdf: RawPdfFile
    ) -> None:
        examination = Examination.objects.create(name=f"gastroscopy-{uuid4().hex[:8]}")

        response = client.post(
            f"/api/media/pdfs/{pdf.pk}/case-resolution/",
            data={
                "action": "create",
                "new_patient": {
                    "first_name": "Erika",
                    "last_name": "Neu",
                    "dob": "1980-05-04",
                },
                "examination_name": examination.name,
                "date_start": "2025-11-29",
            },
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        pdf.refresh_from_db()

        created_patient = Patient.objects.get(pk=payload["patient_id"])
        created_patient_examination = PatientExamination.objects.get(
            pk=payload["patient_examination_id"]
        )

        assert created_patient.first_name == "Erika"
        assert created_patient.last_name == "Neu"
        assert str(created_patient.dob) == "1980-05-04"
        assert (
            created_patient.center_id
            == self._require_sensitive_meta(pdf.sensitive_meta).center_id
        )
        assert (
            created_patient.gender_id
            == self._require_sensitive_meta(pdf.sensitive_meta).patient_gender_id
        )
        assert created_patient_examination.patient_id == created_patient.pk
        assert created_patient_examination.examination_id == examination.pk
        assert pdf.patient_id == created_patient.pk
        assert pdf.examination_id == created_patient_examination.pk
        assert pdf.anonym_examination_report_id is not None

    def test_post_pdf_case_resolution_create_requires_explicit_patient_and_examination(
        self, client: DjangoClient, pdf: RawPdfFile
    ) -> None:
        response = client.post(
            f"/api/media/pdfs/{pdf.pk}/case-resolution/",
            data={"action": "create"},
            content_type="application/json",
        )

        assert response.status_code == 400, response.content
        payload = response.json()
        assert payload["error"] == "Invalid case resolution payload"

    def test_post_pdf_case_resolution_attach_without_document_type_rolls_back_link(
        self, client: DjangoClient, pdf: RawPdfFile
    ) -> None:
        original_examination_id = pdf.examination_id
        original_patient_id = pdf.patient_id
        pdf.raw_meta = {}
        pdf.save(update_fields=["raw_meta"])
        target_examination = PatientExamination.objects.create(
            patient=self._require_sensitive_meta(pdf.sensitive_meta).pseudo_patient
        )

        response = client.post(
            f"/api/media/pdfs/{pdf.pk}/case-resolution/",
            data={"action": "attach", "patient_examination_id": target_examination.pk},
            content_type="application/json",
        )

        assert response.status_code == 400, response.content
        payload = response.json()
        assert payload["error"] == "Case resolution failed"

        pdf.refresh_from_db()
        assert pdf.examination_id == original_examination_id
        assert pdf.patient_id == original_patient_id
        assert pdf.anonym_examination_report_id is None

    def test_post_video_case_resolution_defer(
        self, client: DjangoClient, video: VideoFile
    ) -> None:
        response = client.post(
            f"/api/media/videos/{video.pk}/case-resolution/",
            data={"action": "defer"},
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        video.refresh_from_db()

        assert payload["action"] == "defer"
        assert payload["status"] == "deferred"
        assert payload["patient_examination_id"] is None
        assert payload["case_resolution"]["match_status"] == "deferred"
        assert payload["case_resolution"]["is_explicitly_resolved"] is False
        assert payload["case_resolution"]["is_deferred"] is True
        assert video.examination_id is None

        read_response = client.get(f"/api/media/videos/{video.pk}/case-resolution/")

        assert read_response.status_code == 200, read_response.content
        read_payload = read_response.json()
        assert read_payload["match_status"] == "deferred"
        assert read_payload["is_deferred"] is True
        assert read_payload["linked_patient_examination_id"] is None

    def test_post_video_case_resolution_attach_rejects_conflicting_primary_video(
        self, client: DjangoClient, video: VideoFile, sensitive_meta: SensitiveMeta
    ) -> None:
        other_video = VideoFile.objects.create(
            center=video.center,
            video_hash=f"video-sm-{uuid4().hex}",
            original_file_name="other-video.mp4",
        )
        occupied_patient_examination = PatientExamination.objects.create(
            patient=self._require_sensitive_meta(sensitive_meta).pseudo_patient,
            video=other_video,
        )

        response = client.post(
            f"/api/media/videos/{video.pk}/case-resolution/",
            data={
                "action": "attach",
                "patient_examination_id": occupied_patient_examination.pk,
            },
            content_type="application/json",
        )

        assert response.status_code == 400, response.content
        payload = response.json()
        assert payload["error"] == "Case resolution failed"

    def test_post_video_case_resolution_attach_has_no_report_side_effect(
        self, client: DjangoClient, video: VideoFile
    ) -> None:
        target_patient = Patient.objects.create(
            first_name="Video",
            last_name="Target",
            patient_hash=f"video-target-{uuid4().hex[:8]}",
        )
        target_patient_examination = PatientExamination.objects.create(
            patient=target_patient
        )
        report_count_before = AnonymExaminationReport.objects.count()

        response = client.post(
            f"/api/media/videos/{video.pk}/case-resolution/",
            data={
                "action": "attach",
                "patient_examination_id": target_patient_examination.pk,
            },
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        video.refresh_from_db()
        assert video.examination_id == target_patient_examination.pk
        assert AnonymExaminationReport.objects.count() == report_count_before

    def test_pdf_validation_auto_resolves_case_and_materializes_report(
        self,
        client: DjangoClient,
        factory: APIRequestFactory,
        user: User,
        pdf: RawPdfFile,
    ) -> None:
        validation_payload = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "patient_gender": "männlich",
            "casenumber": "12345",
            "anonymized_text": "Latest validated report text",
            "file_type": "pdf",
            "document_type": "report_final",
        }

        validation_request = factory.post(
            f"/api/anonymization/{pdf.pk}/validate/",
            data=validation_payload,
            format="json",
        )
        force_authenticate(validation_request, user=user)
        validation_view = AnonymizationValidateView.as_view()
        response = self._call_view(validation_view, validation_request, file_id=pdf.pk)

        data = json.loads(response.content)

        assert response.status_code == status.HTTP_200_OK

        pdf.refresh_from_db()
        identity_commit = AuditLedger.objects.filter(
            object_type="SensitiveMeta",
            object_pk=str(pdf.sensitive_meta_id),
            action="identity_committed",
        ).latest("ts")
        linked_patient_examination_id = data["case_resolution"][
            "patient_examination_id"
        ]
        assert pdf.anonymized_text == "Latest validated report text"
        assert data["case_resolution"]["status"] == "linked"
        assert linked_patient_examination_id == pdf.examination_id
        assert data["case_resolution"]["created"] is False
        assert data["case_resolution"]["reason"] in {
            "matched_by_hash",
            "already_linked",
        }
        assert pdf.examination_id is not None
        assert pdf.patient_id is not None
        assert pdf.anonym_examination_report_id is not None
        assert isinstance(pdf.raw_meta, dict)
        assert (
            pdf.raw_meta["examination_hash"]
            == self._require_sensitive_meta(pdf.sensitive_meta).examination_hash
        )
        assert (
            identity_commit.data["examination_hash"]
            == self._require_sensitive_meta(pdf.sensitive_meta).examination_hash
        )
        assert (
            identity_commit.data["linked_patient_examination_id"]
            == linked_patient_examination_id
        )
        assert len(identity_commit.hash) == 64
        assert len(identity_commit.prev_hash) == 64
        assert AuditLedger.verify_chain() is True
        ledger_head = LedgerHead.objects.get(pk=1)
        assert ledger_head.current_hash == identity_commit.hash
        assert ledger_head.last_entry_id == identity_commit.pk
        assert (
            pdf.raw_meta["pseudo_examination_id"]
            == self._require_sensitive_meta(pdf.sensitive_meta).pseudo_examination_id
        )
        assert (
            self._require_anonym_report(pdf.anonym_examination_report).text
            == "Latest validated report text"
        )
        assert (
            self._require_anonym_report(
                pdf.anonym_examination_report
            ).patient_examination_id
            == linked_patient_examination_id
        )

        read_response = client.get(f"/api/media/pdfs/{pdf.pk}/case-resolution/")

        assert read_response.status_code == 200, read_response.content
        read_payload = read_response.json()
        assert read_payload["match_status"] == "linked"
        assert read_payload["is_explicitly_resolved"] is False
        assert read_payload["is_auto_resolved"] is True
        assert (
            read_payload["linked_patient_examination_id"]
            == linked_patient_examination_id
        )
        assert (
            read_payload["pseudo_examination"]["linked_patient_examination_id"]
            == linked_patient_examination_id
        )

    def test_create_anonymized_record_preserves_validated_identity(
        self, sensitive_meta: SensitiveMeta
    ) -> None:
        committed_identity = {
            "patient_hash": sensitive_meta.patient_hash,
            "examination_hash": sensitive_meta.examination_hash,
            "pseudo_patient_id": sensitive_meta.pseudo_patient_id,
            "pseudo_examination_id": sensitive_meta.pseudo_examination_id,
        }

        sensitive_meta.create_anonymized_record()
        sensitive_meta.refresh_from_db()

        assert sensitive_meta.patient_hash == committed_identity["patient_hash"]
        assert sensitive_meta.examination_hash == committed_identity["examination_hash"]
        assert (
            sensitive_meta.pseudo_patient_id == committed_identity["pseudo_patient_id"]
        )
        assert (
            sensitive_meta.pseudo_examination_id
            == committed_identity["pseudo_examination_id"]
        )

    def test_ledger_integrity_detects_tampering(self, user: User) -> None:
        first = AuditLedger.append_identity_commit(
            user=user,
            object_type="SensitiveMeta",
            object_pk="1",
            data={"payload_hash": "first", "examination_hash": "exam-1"},
        )
        second = AuditLedger.append_identity_commit(
            user=user,
            object_type="SensitiveMeta",
            object_pk="2",
            data={"payload_hash": "second", "examination_hash": "exam-2"},
        )

        assert first is not None
        assert second is not None
        assert AuditLedger.verify_chain() is True

        AuditLedger.objects.filter(pk=first.pk).update(
            data={"payload_hash": "tampered", "examination_hash": "exam-1"}
        )

        assert AuditLedger.verify_chain() is False

    def test_patch_pdf_sensitive_metadata(
        self, client: DjangoClient, pdf: RawPdfFile
    ) -> None:
        response = client.patch(
            f"/api/media/pdfs/{pdf.pk}/sensitive-metadata/",
            data={"patient_first_name": "Anna"},
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["pdf_id"] == pdf.pk
        assert payload["sensitive_meta"]["patient_first_name"] == "Anna"

    def test_verify_pdf_sensitive_metadata(
        self, client: DjangoClient, pdf: RawPdfFile
    ) -> None:
        response = client.post(
            f"/api/media/pdfs/{pdf.pk}/sensitive-metadata/verify/",
            data={"dob_verified": True, "names_verified": False},
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["pdf_id"] == pdf.pk
        assert payload["state_verified"] in (True, False)

    def test_get_sensitive_metadata_pk_by_media_type(
        self, client: DjangoClient, video: VideoFile, pdf: RawPdfFile
    ) -> None:
        video_response = client.get(f"/api/media/sensitive-media-id/{video.pk}/video/")
        assert video_response.status_code == 200, video_response.content
        assert video_response.json()["sm"] == video.sensitive_meta_id

        pdf_response = client.get(f"/api/media/sensitive-media-id/{pdf.pk}/pdf/")
        assert pdf_response.status_code == 200, pdf_response.content
        assert pdf_response.json()["sm"] == pdf.sensitive_meta_id

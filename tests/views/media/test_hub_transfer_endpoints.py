from __future__ import annotations

# pyright: reportUnknownMemberType=false

import logging
import hashlib
from collections.abc import Mapping
from typing import Protocol, cast

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import override_settings

from endoreg_db.models import (
    Center,
    EndoscopyProcessor,
    Examiner,
    Frame,
    ImageClassificationAnnotation,
    NetworkNode,
    PatientExaminationReport,
    PortalUserInfo,
    TransferJob,
    VideoFile,
)
from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)
from lx_dtypes.models.contracts.hub_transfer import (
    HubTransferVideoFilePayloadData,
    HubTransferReportTransferPayloadData,
    HubTransferVideoTransferPayloadData,
    validate_hub_transfer_report_payload,
    validate_hub_transfer_video_payload,
)
from tests.helpers.data_loader import load_gender_data


class _ResponseLike(Protocol):
    status_code: int
    content: bytes

    def json(self) -> dict[str, object]: ...


class _StructuredEventRecord(Protocol):
    structured_event: dict[str, object]


@override_settings(ENDOREG_ENABLE_HUB_TRANSFERS=True)
class HubTransferEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        load_gender_data()

    def setUp(self):
        self.center = Center.objects.create(name="center-a", display_name="Center A")
        self.processor = EndoscopyProcessor.objects.create(
            name="hub-test-processor",
            image_width=1920,
            image_height=1080,
            endoscope_image_x=0,
            endoscope_image_y=0,
            endoscope_image_width=0,
            endoscope_image_height=0,
            examination_date_x=0,
            examination_date_y=0,
            examination_date_width=0,
            examination_date_height=0,
            patient_first_name_x=0,
            patient_first_name_y=0,
            patient_first_name_width=0,
            patient_first_name_height=0,
            patient_last_name_x=0,
            patient_last_name_y=0,
            patient_last_name_width=0,
            patient_last_name_height=0,
            patient_dob_x=0,
            patient_dob_y=0,
            patient_dob_width=0,
            patient_dob_height=0,
        )
        self.processor.centers.add(self.center)
        self.source_node = NetworkNode.objects.create(
            display_name="Site A Node",
            node_key="site-a-node",
            role=NetworkNode.Role.SITE_NODE,
            owning_center=self.center,
        )
        self.source_secret = "site-a-secret"
        self.source_node.set_shared_secret(self.source_secret)
        self.source_node.save(update_fields=["shared_secret_hash"])
        self.target_node = NetworkNode.objects.create(
            display_name="Study Hub",
            node_key="study-hub",
            role=NetworkNode.Role.CENTRAL_HUB,
        )

    def _auth_headers(self) -> dict[str, str]:
        return {
            "X-Network-Node-Key": self.source_node.node_key,
            "X-Network-Node-Secret": self.source_secret,
            "X-Client-Cert-Verified": "SUCCESS",
        }

    def _secure_post(
        self,
        path: str,
        *,
        data: Mapping[str, object] | None,
        content_type: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> _ResponseLike:
        if content_type is None:
            return cast(
                _ResponseLike,
                self.client.post(path, data=data, secure=True, headers=headers),
            )
        return cast(
            _ResponseLike,
            self.client.post(
                path,
                data=data,
                secure=True,
                content_type=content_type,
                headers=headers,
            ),
        )

    def _secure_get(
        self,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> _ResponseLike:
        return cast(_ResponseLike, self.client.get(path, secure=True, headers=headers))

    @staticmethod
    def _structured_event(record: logging.LogRecord) -> dict[str, object]:
        return cast(_StructuredEventRecord, record).structured_event

    @staticmethod
    def _sha256(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _video_transfer_payload(
        self,
        *,
        transfer_key: str,
        video_hash: str,
        transfer_mode: str = "metadata_only",
        processing_policy: str = "reprocess_if_missing_outputs",
        sender_processing_success: bool = False,
        processed_video_hash: str | None = None,
        examination_date: str = "2026-03-20",
    ) -> HubTransferVideoTransferPayloadData:
        video_file_payload: HubTransferVideoFilePayloadData = {
            "video_hash": video_hash,
            "original_file_name": "example.mp4",
            "suffix": ".mp4",
            "fps": 25.0,
            "duration": 12.5,
            "frame_count": 300,
            "width": 1280,
            "height": 720,
            "meta": {"origin": "site-a"},
        }
        if processed_video_hash:
            video_file_payload["processed_video_hash"] = processed_video_hash

        payload: dict[str, object] = {
            "transfer_key": transfer_key,
            "source_node_key": self.source_node.node_key,
            "target_node_key": self.target_node.node_key,
            "source_center_key": self.center.center_key,
            "resource_kind": "video",
            "resource_hash": video_hash,
            "transfer_mode": transfer_mode,
            "processing_policy": processing_policy,
            "processing_intent": "sender_requests_state_preservation",
            "cleanup_policy": "retain_all",
            "resource_rows": {
                "video_file": video_file_payload,
                "sensitive_meta": {
                    "patient_first_name": "Max",
                    "patient_last_name": "Mustermann",
                    "patient_dob": "1990-01-01",
                    "examination_date": examination_date,
                },
                "video_state": {
                    "processing_started": True,
                    "frames_extracted": True,
                    "sensitive_meta_processed": True,
                },
                "processing_history": {
                    "file_hash": video_hash,
                    "success": True,
                },
            },
            "processing_snapshot": {
                "sender_processing_success": sender_processing_success,
            },
        }
        return validate_hub_transfer_video_payload(payload)

    def _report_transfer_payload(
        self,
        *,
        transfer_key: str,
        pdf_hash: str,
        transfer_mode: str = "metadata_only",
        processing_policy: str = "reprocess_if_missing_outputs",
        sender_processing_success: bool = False,
    ) -> HubTransferReportTransferPayloadData:
        payload: dict[str, object] = {
            "transfer_key": transfer_key,
            "source_node_key": self.source_node.node_key,
            "target_node_key": self.target_node.node_key,
            "source_center_key": self.center.center_key,
            "resource_kind": "report",
            "resource_hash": pdf_hash,
            "transfer_mode": transfer_mode,
            "processing_policy": processing_policy,
            "processing_intent": "sender_requests_state_preservation",
            "cleanup_policy": "retain_all",
            "resource_rows": {
                "raw_pdf_file": {
                    "pdf_hash": pdf_hash,
                    "text": "Clinical report",
                },
                "sensitive_meta": {
                    "patient_first_name": "Max",
                    "patient_last_name": "Mustermann",
                    "patient_dob": "1990-01-01",
                    "examination_date": "2026-03-20",
                },
                "raw_pdf_state": {
                    "processing_started": True,
                    "text_meta_extracted": True,
                    "sensitive_meta_processed": True,
                },
                "processing_history": {
                    "file_hash": pdf_hash,
                    "success": sender_processing_success,
                },
            },
            "processing_snapshot": {
                "sender_processing_success": sender_processing_success,
            },
        }
        return validate_hub_transfer_report_payload(payload)

    @override_settings(
        ENDOREG_DEPLOYMENT_ROLE="central_hub",
        ENDOREG_ENABLE_HUB_TRANSFERS=False,
    )
    def test_transfer_endpoints_return_404_when_feature_flag_is_disabled(self):
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__disabled",
            video_hash="hash-disabled",
        )

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert response.status_code == 404, response.content
        assert not TransferJob.objects.filter(
            transfer_key="site-a__video__disabled"
        ).exists()

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
    def test_transfer_endpoints_return_404_in_local_study_server(self):
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__local-study-disabled",
            video_hash="hash-local-disabled",
        )

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert response.status_code == 404, response.content
        assert not TransferJob.objects.filter(
            transfer_key="site-a__video__local-study-disabled"
        ).exists()

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_registration_creates_placeholder_video_and_waits_for_media(self):
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__hash-1",
            video_hash="hash-1",
        )

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert response.status_code == 201, response.content
        body = response.json()
        assert body["transfer_status"] == "awaiting_media"
        assert body["processing_decision"] == "wait_for_missing_media"

        video = VideoFile.objects.get(video_hash="hash-1")
        assert video.center == self.center
        assert video.original_file_name == "example.mp4"
        assert video.state is not None
        assert video.state.processing_started is True
        assert ProcessingHistory.objects.get(file_hash="hash-1").success is True
        transfer_job = TransferJob.objects.get(transfer_key=payload["transfer_key"])
        assert transfer_job.case_resolution_status == "linked"
        assert transfer_job.linked_patient_id is not None
        assert transfer_job.linked_patient_examination_id is not None
        assert transfer_job.provenance["entrypoint"] == "transfer"
        assert transfer_job.provenance["source_node_key"] == self.source_node.node_key
        assert transfer_job.provenance["target_node_key"] == self.target_node.node_key
        assert transfer_job.provenance["source_center_key"] == self.center.center_key
        assert (
            transfer_job.provenance["cleanup_policy"]
            == TransferJob.CleanupPolicy.RETAIN_ALL
        )
        assert transfer_job.cleanup_status == TransferJob.CleanupStatus.NOT_REQUESTED

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_video_transfer_imports_frame_annotations_and_related_reports(self):
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__annotations",
            video_hash="hash-annotations",
        )
        payload["resource_rows"]["frame_annotations"] = [
            {
                "annotation_id": 42,
                "video_hash": "hash-annotations",
                "frame_number": 5,
                "frame_relative_path": "frames/frame_000005.jpg",
                "frame_timestamp": 0.2,
                "label_name": "lesion_visible",
                "value": True,
                "float_value": 0.91,
                "annotator": "site-a-reviewer",
                "information_source_name": "manual_annotation",
            }
        ]
        payload["resource_rows"]["reports"] = [
            {
                "id": 77,
                "patient_examination": 123,
                "template_name": "star_upper_gi_main",
                "template_version": "2026.1",
                "template_hash": "template-hash",
                "title": "Transferred upper GI report",
                "status": "final",
                "editor_payload": {"sections": [{"id": "findings"}]},
                "patient_context_snapshot": {"patient_hash": "patient-hash"},
                "history_context_snapshot": {"previous_reports": []},
                "rendered_text": "Anonymized report text",
                "version": 2,
                "is_active": True,
                "finalized_at": "2026-05-20T10:30:00Z",
            }
        ]

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert response.status_code == 201, response.content

        video = VideoFile.objects.get(video_hash="hash-annotations")
        frame = Frame.objects.get(video=video, frame_number=5)
        assert frame.relative_path == "frames/frame_000005.jpg"
        assert frame.timestamp == 0.2

        annotation = ImageClassificationAnnotation.objects.select_related(
            "label",
            "information_source",
        ).get(frame=frame)
        assert annotation.label.name == "lesion_visible"
        assert annotation.information_source is not None
        assert annotation.information_source.name == "manual_annotation"
        assert annotation.value is True
        assert annotation.float_value == 0.91
        assert (
            annotation.external_annotation_id
            == f"hub_transfer:{self.source_node.node_key}:annotation:42"
        )
        assert video.state is not None
        video.state.refresh_from_db()
        assert video.state.frame_annotations_generated is True

        transfer_job = TransferJob.objects.get(transfer_key=payload["transfer_key"])
        report = PatientExaminationReport.objects.get(
            patient_examination_id=transfer_job.linked_patient_examination_id,
            template_name="star_upper_gi_main",
            version=2,
        )
        assert report.status == PatientExaminationReport.Status.FINAL
        assert report.title == "Transferred upper GI report"
        assert report.editor_payload == {"sections": [{"id": "findings"}]}
        assert report.rendered_text == "Anonymized report text"
        assert report.finalized_at is not None

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_registration_requires_matching_node_credentials(self):
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__auth-fail",
            video_hash="hash-auth-fail",
        )

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers={
                "X-Network-Node-Key": "wrong-node",
                "X-Network-Node-Secret": self.source_secret,
                "X-Client-Cert-Verified": "SUCCESS",
            },
        )

        assert response.status_code == 403, response.content
        assert not TransferJob.objects.filter(
            transfer_key="site-a__video__auth-fail"
        ).exists()

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_registration_fails_closed_without_shared_secret_hash(self):
        self.source_node.shared_secret_hash = ""
        self.source_node.save(update_fields=["shared_secret_hash"])
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__missing-secret-hash",
            video_hash="hash-missing-secret-hash",
        )

        with self.assertLogs("endoreg_db.hub.audit", level="INFO") as audit_logs:
            response = self._secure_post(
                "/api/media/hub/transfers/",
                data=payload,
                content_type="application/json",
                headers=self._auth_headers(),
            )

        assert response.status_code == 403, response.content
        detail = cast(str, response.json()["detail"])
        assert "Invalid network node credentials" in detail
        assert not TransferJob.objects.filter(
            transfer_key="site-a__video__missing-secret-hash"
        ).exists()
        log_output = "\n".join(audit_logs.output)
        assert "hub.transfer_node_auth_failed" in log_output
        assert "missing_shared_secret_hash" in log_output
        assert self.source_secret not in log_output

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_registration_does_not_allow_authenticated_user_to_bypass_node_auth(
        self,
    ):
        user = User.objects.create(
            username="hub-admin",
            email="hub-admin@example.org",
        )
        user.set_password("secret")
        user.save(update_fields=["password"])
        self.client.force_login(user)
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__user-bypass-denied",
            video_hash="hash-user-bypass-denied",
        )

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
        )

        assert response.status_code == 403, response.content
        detail = cast(str, response.json()["detail"])
        assert "Invalid network node credentials" in detail
        assert not TransferJob.objects.filter(
            transfer_key="site-a__video__user-bypass-denied"
        ).exists()

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_registration_rejects_authenticated_user_without_center_scope(
        self,
    ):
        user = User.objects.create(
            username="unscoped-transfer-user",
        )
        user.set_password("secret")
        user.save(update_fields=["password"])
        self.client.force_login(user)
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__unscoped-user",
            video_hash="hash-unscoped-user",
        )

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert response.status_code == 403, response.content
        detail = cast(str, response.json()["detail"])
        assert "do not have access" in detail

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_registration_requires_secure_transport(self):
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__insecure-transport",
            video_hash="hash-insecure-transport",
        )

        with self.assertLogs(
            "endoreg_db.views.media.hub.transfers",
            level="WARNING",
        ) as transfer_logs:
            response = self.client.post(
                "/api/media/hub/transfers/",
                data=payload,
                content_type="application/json",
                headers=self._auth_headers(),
            )

        assert response.status_code == 403, response.content
        detail = cast(str, response.json()["detail"])
        assert "requires HTTPS" in detail
        event = self._structured_event(transfer_logs.records[-1])
        assert event["event"] == "hub.transfer_secure_transport_failed"
        assert event["reason"] == "insecure_request"
        assert self.source_secret not in "\n".join(transfer_logs.output)

    @override_settings(
        ENDOREG_DEPLOYMENT_ROLE="central_hub",
        ENDOREG_HUB_TRANSFER_REQUIRE_MTLS=True,
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    )
    def test_transfer_registration_accepts_proxy_https_header(self):
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__proxy-https",
            video_hash="hash-proxy-https",
        )

        response = self.client.post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers={**self._auth_headers(), "X-Forwarded-Proto": "https"},
        )

        assert response.status_code == 201, response.content
        assert TransferJob.objects.filter(
            transfer_key="site-a__video__proxy-https"
        ).exists()

    @override_settings(
        ENDOREG_DEPLOYMENT_ROLE="central_hub",
        ENDOREG_HUB_TRANSFER_REQUIRE_MTLS=True,
    )
    def test_transfer_registration_requires_proxy_verified_mtls(self):
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__mtls-required",
            video_hash="hash-mtls-required",
        )

        with self.assertLogs(
            "endoreg_db.views.media.hub.transfers",
            level="WARNING",
        ) as transfer_logs:
            response = self._secure_post(
                "/api/media/hub/transfers/",
                data=payload,
                content_type="application/json",
                headers={
                    "X-Network-Node-Key": self.source_node.node_key,
                    "X-Network-Node-Secret": self.source_secret,
                },
            )

        assert response.status_code == 403, response.content
        detail = cast(str, response.json()["detail"])
        assert "mutual TLS" in detail
        event = self._structured_event(transfer_logs.records[-1])
        assert event["event"] == "hub.transfer_mtls_check_failed"
        assert event["reason"] == "mtls_proxy_verification_failed"
        assert event["mtls_actual_value_present"] is False
        assert "SUCCESS" not in "\n".join(transfer_logs.output)
        assert self.source_secret not in "\n".join(transfer_logs.output)

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_registration_is_idempotent_for_same_transfer_key(self):
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__hash-2",
            video_hash="hash-2",
        )

        first = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )
        second = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert first.status_code == 201, first.content
        assert second.status_code == 200, second.content
        assert first.json()["id"] == second.json()["id"]
        assert (
            TransferJob.objects.filter(transfer_key=payload["transfer_key"]).count()
            == 1
        )

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_skips_reprocessing_when_local_success_exists(self):
        video = VideoFile.objects.create(
            video_hash="hash-3",
            center=self.center,
            processed_file=SimpleUploadedFile(
                "hash-3-processed.mp4",
                b"processed-video",
                content_type="video/mp4",
            ),
        )
        ProcessingHistory.mark_success(file_hash=video.video_hash, obj=video)

        payload = self._video_transfer_payload(
            transfer_key="site-a__video__hash-3",
            video_hash="hash-3",
        )

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert response.status_code == 201, response.content
        body = response.json()
        assert body["transfer_status"] == "applied"
        assert body["processing_decision"] == "skip_processing_existing_success"

        status_response = self._secure_get(
            f"/api/media/hub/transfers/{payload['transfer_key']}/status/",
            headers=self._auth_headers(),
        )
        assert status_response.status_code == 200, status_response.content
        assert (
            status_response.json()["processing_decision"]
            == "skip_processing_existing_success"
        )

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfers_for_same_patient_join_by_sensitive_meta_hash_inputs(self):
        first_payload = self._video_transfer_payload(
            transfer_key="site-a__video__join-1",
            video_hash="join-hash-1",
        )
        second_payload = self._video_transfer_payload(
            transfer_key="site-a__video__join-2",
            video_hash="join-hash-2",
            examination_date="2026-03-21",
        )

        first_response = self._secure_post(
            "/api/media/hub/transfers/",
            data=first_payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )
        second_response = self._secure_post(
            "/api/media/hub/transfers/",
            data=second_payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert first_response.status_code == 201, first_response.content
        assert second_response.status_code == 201, second_response.content

        first_transfer = TransferJob.objects.get(
            transfer_key=first_payload["transfer_key"]
        )
        second_transfer = TransferJob.objects.get(
            transfer_key=second_payload["transfer_key"]
        )

        assert first_transfer.case_resolution_status == "linked"
        assert second_transfer.case_resolution_status == "linked"
        assert first_transfer.linked_patient_id == second_transfer.linked_patient_id
        assert (
            first_transfer.linked_patient_examination_id
            != second_transfer.linked_patient_examination_id
        )

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_registration_rejects_raw_media_transfer_modes(self):
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__raw-mode-rejected",
            video_hash="hash-raw-mode-rejected",
            transfer_mode="metadata_and_raw_media",
        )

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert response.status_code == 400, response.content
        assert "Raw media transfer is not permitted" in str(response.json())

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_report_transfer_imports_related_lx_report_rows(self):
        payload = self._report_transfer_payload(
            transfer_key="site-a__report__lx-report",
            pdf_hash="hash-lx-report",
        )
        payload["resource_rows"]["reports"] = [
            {
                "id": 88,
                "patient_examination": 321,
                "template_name": "star_colonoscopy_main",
                "template_version": "2026.1",
                "template_hash": "report-template-hash",
                "title": "Transferred colonoscopy report",
                "status": "draft",
                "editor_payload": {"sections": [{"id": "summary"}]},
                "rendered_text": "Draft anonymized report text",
                "version": 1,
                "is_active": True,
            }
        ]

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert response.status_code == 201, response.content

        transfer_job = TransferJob.objects.get(transfer_key=payload["transfer_key"])
        report = PatientExaminationReport.objects.get(
            patient_examination_id=transfer_job.linked_patient_examination_id,
            template_name="star_colonoscopy_main",
        )
        assert report.status == PatientExaminationReport.Status.DRAFT
        assert report.title == "Transferred colonoscopy report"
        assert report.editor_payload == {"sections": [{"id": "summary"}]}
        assert report.rendered_text == "Draft anonymized report text"

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_registration_requires_anonymized_status(self):
        payload = self._report_transfer_payload(
            transfer_key="site-a__report__not-anonymized",
            pdf_hash="hash-not-anonymized",
        )
        payload["resource_rows"]["raw_pdf_state"] = {
            "processing_started": True,
            "text_meta_extracted": True,
        }

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert response.status_code == 400, response.content
        assert "only allowed for anonymized data" in str(response.json())

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_raw_video_upload_is_rejected(self):
        raw_bytes = b"raw-video-bytes"
        video_hash = self._sha256(raw_bytes)
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__raw-upload",
            video_hash=video_hash,
        )

        create_response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )
        assert create_response.status_code == 201, create_response.content

        with self.assertLogs(
            "endoreg_db.views.media.hub.transfers",
            level="WARNING",
        ) as transfer_logs:
            upload_response = self._secure_post(
                f"/api/media/hub/transfers/{payload['transfer_key']}/media/",
                data={
                    "media_role": "raw",
                    "file": SimpleUploadedFile(
                        "source.mp4",
                        raw_bytes,
                        content_type="video/mp4",
                    ),
                },
                headers=self._auth_headers(),
            )

        assert upload_response.status_code == 400, upload_response.content
        assert "Only anonymized processed media may be uploaded" in str(
            upload_response.json()
        )
        event = self._structured_event(transfer_logs.records[-1])
        assert event["event"] == "hub.transfer_media_upload_validation_failed"
        assert event["error_fields"] == ["media_role"]
        log_output = "\n".join(transfer_logs.output)
        assert "raw-video-bytes" not in log_output
        assert "source.mp4" not in log_output

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_media_upload_requires_multipart_file(self):
        processed_hash = self._sha256(b"processed-video")
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__missing-media-file",
            video_hash="hash-missing-media-file",
            transfer_mode="metadata_and_processed_media",
            sender_processing_success=True,
            processed_video_hash=processed_hash,
        )

        create_response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )
        assert create_response.status_code == 201, create_response.content

        upload_response = self._secure_post(
            f"/api/media/hub/transfers/{payload['transfer_key']}/media/",
            data={"media_role": "processed"},
            headers=self._auth_headers(),
        )

        assert upload_response.status_code == 400, upload_response.content
        assert "multipart file upload is required" in str(upload_response.json())

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_processed_video_upload_preserves_sender_state(self):
        raw_hash = self._sha256(b"raw-video")
        processed_bytes = b"processed-video"
        processed_hash = self._sha256(processed_bytes)
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__processed-upload",
            video_hash=raw_hash,
            transfer_mode="metadata_and_processed_media",
            processing_policy="preserve_processing_state",
            sender_processing_success=True,
            processed_video_hash=processed_hash,
        )

        create_response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )
        assert create_response.status_code == 201, create_response.content

        upload_response = self._secure_post(
            f"/api/media/hub/transfers/{payload['transfer_key']}/media/",
            data={
                "media_role": "processed",
                "file": SimpleUploadedFile(
                    "processed.mp4",
                    processed_bytes,
                    content_type="video/mp4",
                ),
            },
            headers=self._auth_headers(),
        )

        assert upload_response.status_code == 200, upload_response.content
        body = upload_response.json()
        assert body["transfer_status"] == "applied"
        assert body["processing_decision"] == "skip_processing_preserved_state"

        video = VideoFile.objects.get(video_hash=raw_hash)
        assert video.processed_video_hash == processed_hash
        assert ProcessingHistory.objects.get(file_hash=raw_hash).success is True

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_raw_report_upload_is_rejected(self):
        report_bytes = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
        pdf_hash = self._sha256(report_bytes)
        payload = self._report_transfer_payload(
            transfer_key="site-a__report__raw-upload",
            pdf_hash=pdf_hash,
        )

        create_response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )
        assert create_response.status_code == 201, create_response.content

        upload_response = self._secure_post(
            f"/api/media/hub/transfers/{payload['transfer_key']}/media/",
            data={
                "media_role": "raw",
                "file": SimpleUploadedFile(
                    "report.pdf",
                    report_bytes,
                    content_type="application/pdf",
                ),
            },
            headers=self._auth_headers(),
        )

        assert upload_response.status_code == 400, upload_response.content
        assert "Only anonymized processed media may be uploaded" in str(
            upload_response.json()
        )

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_media_upload_is_center_scoped_for_authenticated_users(self):
        other_center = Center.objects.create(
            name="center-b",
            display_name="Center B",
        )
        examiner = Examiner.objects.create(
            first_name="Scoped",
            last_name="TransferUser",
            hash="scoped-transfer-user-hash",
            center=other_center,
        )
        user = User.objects.create(
            username="scoped-transfer-user",
        )
        user.set_password("secret")
        user.save(update_fields=["password"])
        PortalUserInfo.objects.create(user=user, examiner=examiner)

        processed_bytes = b"processed-video"
        processed_hash = self._sha256(processed_bytes)
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__center-scoped-media",
            video_hash=self._sha256(b"raw-video"),
            transfer_mode="metadata_and_processed_media",
            processing_policy="preserve_processing_state",
            sender_processing_success=True,
            processed_video_hash=processed_hash,
        )

        create_response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )
        assert create_response.status_code == 201, create_response.content

        self.client.force_login(user)
        upload_response = self._secure_post(
            f"/api/media/hub/transfers/{payload['transfer_key']}/media/",
            data={
                "media_role": "processed",
                "file": SimpleUploadedFile(
                    "processed.mp4",
                    processed_bytes,
                    content_type="video/mp4",
                ),
            },
            headers=self._auth_headers(),
        )

        assert upload_response.status_code == 404, upload_response.content

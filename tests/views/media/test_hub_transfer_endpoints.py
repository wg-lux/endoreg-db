from __future__ import annotations

# pyright: reportUnknownMemberType=false

import logging
import hashlib
import base64
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, TypedDict, cast
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import override_settings
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from lx_dtypes.models.contracts.hub_media_envelope import HubMediaEnvelopeMetadata

from endoreg_db.models import (
    Center,
    EndoscopyProcessor,
    Examiner,
    Frame,
    ImageClassificationAnnotation,
    LabelVideoSegment,
    NetworkNode,
    PatientExaminationReport,
    PortalUserInfo,
    RawPdfFile,
    TransferJob,
    VideoFile,
    VideoState,
)
from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)
from endoreg_db.services.hub.transfer_envelope import HubMediaEnvelopeError
from tests.helpers.data_loader import load_gender_data


class _ResponseLike(Protocol):
    status_code: int
    content: bytes

    def json(self) -> dict[str, object]: ...


class _StructuredEventRecord(Protocol):
    structured_event: dict[str, object]


class _EnvelopedUploadData(TypedDict):
    media_role: str
    envelope: str
    ciphertext: bytes


@override_settings(ENDOREG_ENABLE_HUB_TRANSFERS=True)
class HubTransferEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        load_gender_data()

    def setUp(self):
        self._key_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._key_directory.cleanup)
        self._recipient_private_key = X25519PrivateKey.generate()
        recipient_private_key_path = Path(self._key_directory.name) / "recipient.pem"
        recipient_private_key_path.write_bytes(
            self._recipient_private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        recipient_private_key_path.chmod(0o600)
        key_settings = override_settings(
            ENDOREG_HUB_TRANSFER_RECIPIENT_PRIVATE_KEY_FILES=(
                recipient_private_key_path,
            ),
            ENDOREG_HUB_TRANSFER_REQUIRE_ROOT_OWNED_PRIVATE_KEYS=False,
        )
        key_settings.enable()
        self.addCleanup(key_settings.disable)
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
        data: Mapping[str, object] | bytes | None,
        content_type: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> _ResponseLike:
        if isinstance(data, Mapping):
            media_role = data.get("media_role")
            envelope_json = data.get("envelope")
            ciphertext = data.get("ciphertext")
            if (
                isinstance(media_role, str)
                and isinstance(envelope_json, str)
                and isinstance(ciphertext, bytes)
            ):
                raw_headers = dict(headers or {})
                raw_headers["X-Hub-Media-Role"] = media_role
                raw_headers["X-Hub-Media-Envelope"] = envelope_json
                return cast(
                    _ResponseLike,
                    self.client.post(
                        path,
                        data=ciphertext,
                        secure=True,
                        content_type="application/octet-stream",
                        headers=raw_headers,
                    ),
                )
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

    @staticmethod
    def _b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    def _enveloped_upload_data(
        self,
        *,
        transfer_key: str,
        plaintext: bytes,
        filename: str,
    ) -> _EnvelopedUploadData:
        del filename
        transfer_job = TransferJob.objects.select_related(
            "source_node", "source_center", "target_node"
        ).get(transfer_key=transfer_key)
        plaintext_hash = self._sha256(plaintext)
        recipient_public_key = self._recipient_private_key.public_key()
        recipient_key_id = self._sha256(
            recipient_public_key.public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        )
        ephemeral_private_key = X25519PrivateKey.generate()
        ephemeral_public_key = ephemeral_private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        wrap_salt = os.urandom(16)
        wrap_nonce = os.urandom(12)
        payload_nonce = os.urandom(12)
        data_encryption_key = os.urandom(32)
        source_center = transfer_job.source_center
        assert source_center is not None
        metadata_values = {
            "transfer_key": transfer_job.transfer_key,
            "source_node_key": transfer_job.source_node.node_key,
            "source_center_key": source_center.center_key,
            "target_node_key": transfer_job.target_node.node_key,
            "resource_kind": transfer_job.resource_kind,
            "resource_hash": transfer_job.resource_hash,
            "processed_media_hash": plaintext_hash,
            "plaintext_sha256": plaintext_hash,
            "plaintext_size": len(plaintext),
            "recipient_key_id": recipient_key_id,
            "ephemeral_public_key": self._b64(ephemeral_public_key),
            "wrap_salt": self._b64(wrap_salt),
            "wrap_nonce": self._b64(wrap_nonce),
            "wrapped_data_encryption_key": self._b64(b"0" * 48),
            "payload_nonce": self._b64(payload_nonce),
            "payload_tag": self._b64(b"0" * 16),
        }
        provisional = HubMediaEnvelopeMetadata.model_validate(metadata_values)
        wrapping_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=wrap_salt,
            info=b"lx-hub-media-envelope-wrap-v1",
        ).derive(ephemeral_private_key.exchange(recipient_public_key))
        wrapped_key = AESGCM(wrapping_key).encrypt(
            wrap_nonce,
            data_encryption_key,
            provisional.authenticated_data(),
        )
        metadata_values["wrapped_data_encryption_key"] = self._b64(wrapped_key)
        provisional = HubMediaEnvelopeMetadata.model_validate(metadata_values)
        encryptor = Cipher(
            algorithms.AES(data_encryption_key), modes.GCM(payload_nonce)
        ).encryptor()
        encryptor.authenticate_additional_data(provisional.authenticated_data())
        ciphertext = encryptor.update(plaintext) + encryptor.finalize()
        metadata_values["payload_tag"] = self._b64(encryptor.tag)
        metadata = HubMediaEnvelopeMetadata.model_validate(metadata_values)
        # Wrapped-key and payload authentication both bind the final metadata AAD.
        wrapped_key = AESGCM(wrapping_key).encrypt(
            wrap_nonce,
            data_encryption_key,
            metadata.authenticated_data(),
        )
        metadata_values["wrapped_data_encryption_key"] = self._b64(wrapped_key)
        metadata = HubMediaEnvelopeMetadata.model_validate(metadata_values)
        encryptor = Cipher(
            algorithms.AES(data_encryption_key), modes.GCM(payload_nonce)
        ).encryptor()
        encryptor.authenticate_additional_data(metadata.authenticated_data())
        ciphertext = encryptor.update(plaintext) + encryptor.finalize()
        metadata_values["payload_tag"] = self._b64(encryptor.tag)
        metadata = HubMediaEnvelopeMetadata.model_validate(metadata_values)
        return {
            "media_role": "processed",
            "envelope": metadata.model_dump_json(),
            "ciphertext": ciphertext,
        }

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
    ) -> dict[str, Any]:
        effective_processed_video_hash = processed_video_hash or self._sha256(
            f"processed:{video_hash}".encode()
        )
        video_file_payload: dict[str, object] = {
            "video_hash": video_hash,
            "processed_video_hash": effective_processed_video_hash,
            "suffix": ".mp4",
            "fps": 25.0,
            "duration": 12.5,
            "frame_count": 300,
            "width": 1280,
            "height": 720,
        }
        patient_hash = self._sha256(b"site-a-patient")
        examination_hash = self._sha256(
            f"site-a-examination:{examination_date}".encode()
        )
        payload: dict[str, Any] = {
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
            "payload_schema_version": "3.0",
            "resource_rows": {
                "video_file": video_file_payload,
                "sensitive_meta": {
                    "patient_hash": patient_hash,
                    "examination_hash": examination_hash,
                },
                "video_state": {
                    "processing_started": True,
                    "frames_extracted": True,
                    "sensitive_meta_processed": True,
                    "anonymized": True,
                    "anonymization_validated": True,
                    "processed_file_sha256": effective_processed_video_hash,
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
        return payload

    def _report_transfer_payload(
        self,
        *,
        transfer_key: str,
        pdf_hash: str,
        transfer_mode: str = "metadata_only",
        processing_policy: str = "reprocess_if_missing_outputs",
        sender_processing_success: bool = False,
        processed_file_sha256: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
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
            "payload_schema_version": "3.0",
            "resource_rows": {
                "raw_pdf_file": {
                    "pdf_hash": pdf_hash,
                    "anonymized_text": "Anonymized clinical report",
                },
                "sensitive_meta": {
                    "patient_hash": self._sha256(b"site-a-patient"),
                    "examination_hash": self._sha256(b"site-a-examination:2026-03-20"),
                },
                "raw_pdf_state": {
                    "processing_started": True,
                    "text_meta_extracted": True,
                    "sensitive_meta_processed": True,
                    "anonymized": True,
                    "anonymization_validated": True,
                    "processed_file_sha256": (
                        processed_file_sha256
                        or self._sha256(f"processed:{pdf_hash}".encode())
                    ),
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
        return payload

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
        assert (
            body["processed_media_hash"]
            == payload["resource_rows"]["video_file"]["processed_video_hash"]
        )

        video = VideoFile.objects.get(video_hash="hash-1")
        assert video.center == self.center
        assert video.original_file_name is None
        assert video.sensitive_meta is not None
        assert video.sensitive_meta.patient_first_name is None
        assert video.sensitive_meta.patient_last_name is None
        assert video.sensitive_meta.patient_dob is None
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
    def test_postgresql_video_registration_locks_existing_row_with_nullable_relations(
        self,
    ):
        if connection.vendor != "postgresql":
            self.skipTest("nullable joined-row locking is PostgreSQL-specific")
        existing_video = VideoFile.objects.create(
            video_hash="hash-existing-nullable-video",
            center=self.center,
        )
        assert existing_video.state_id is None
        assert existing_video.sensitive_meta_id is None
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__existing-nullable-video",
            video_hash=existing_video.video_hash,
        )

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert response.status_code == 201, response.content
        existing_video.refresh_from_db()
        transfer_job = TransferJob.objects.get(transfer_key=payload["transfer_key"])
        assert transfer_job.target_object_id == existing_video.pk
        assert existing_video.state_id is not None
        assert existing_video.sensitive_meta_id is not None

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_postgresql_report_registration_locks_existing_row_with_nullable_relations(
        self,
    ):
        if connection.vendor != "postgresql":
            self.skipTest("nullable joined-row locking is PostgreSQL-specific")
        existing_report = RawPdfFile.objects.create(
            pdf_hash="hash-existing-nullable-report",
            center=self.center,
        )
        assert existing_report.state_id is None
        assert existing_report.sensitive_meta_id is None
        payload = self._report_transfer_payload(
            transfer_key="site-a__report__existing-nullable-report",
            pdf_hash=existing_report.pdf_hash,
        )

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert response.status_code == 201, response.content
        existing_report.refresh_from_db()
        transfer_job = TransferJob.objects.get(transfer_key=payload["transfer_key"])
        assert transfer_job.target_object_id == existing_report.pk
        assert existing_report.state_id is not None
        assert existing_report.sensitive_meta_id is not None

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_registration_returns_typed_model_validation_details(self):
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__model-validation",
            video_hash="hash-model-validation",
        )

        with patch(
            "endoreg_db.views.media.hub.transfers.create_or_reuse_transfer_job",
            side_effect=DjangoValidationError(
                {"resource_rows": ["Typed resource rows are inconsistent."]}
            ),
        ):
            response = self._secure_post(
                "/api/media/hub/transfers/",
                data=payload,
                content_type="application/json",
                headers=self._auth_headers(),
            )

        assert response.status_code == 400, response.content
        assert response.json() == {
            "error": "Transfer payload validation failed",
            "details": {"resource_rows": ["Typed resource rows are inconsistent."]},
        }
        assert not TransferJob.objects.filter(
            transfer_key=payload["transfer_key"]
        ).exists()

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_metadata_validation_rolls_back_registration(self):
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__metadata-validation",
            video_hash="hash-metadata-validation",
        )

        with patch(
            "endoreg_db.views.media.hub.transfers.apply_transfer_metadata",
            side_effect=DjangoValidationError(
                ["Transferred metadata violates the typed model contract."]
            ),
        ):
            response = self._secure_post(
                "/api/media/hub/transfers/",
                data=payload,
                content_type="application/json",
                headers=self._auth_headers(),
            )

        assert response.status_code == 400, response.content
        assert response.json() == {
            "error": "Transfer payload validation failed",
            "details": {
                "non_field_errors": [
                    "Transferred metadata violates the typed model contract."
                ]
            },
        }
        assert not TransferJob.objects.filter(
            transfer_key=payload["transfer_key"]
        ).exists()
        assert not VideoFile.objects.filter(
            video_hash=payload["resource_hash"]
        ).exists()

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_center_scope_uses_authenticated_node_not_django_user(self):
        foreign_center = Center.objects.create(name="foreign-session-center")
        session_user = User.objects.create_user(username="foreign-session-user")
        portal_info = PortalUserInfo.objects.create(user=session_user)
        portal_info.centers.add(foreign_center)
        self.client.force_login(session_user)
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__node-scope",
            video_hash="hash-node-scope",
        )

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert response.status_code == 201, response.content
        transfer_job = TransferJob.objects.get(transfer_key=payload["transfer_key"])
        assert (
            cast(Any, transfer_job).source_center_id
            == cast(Any, self.source_node).owning_center_id
        )

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_registration_rejects_direct_identity_fields(self):
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__pii-rejected",
            video_hash="hash-pii-rejected",
        )
        payload["resource_rows"]["sensitive_meta"]["patient_first_name"] = "Max"

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert response.status_code == 400, response.content
        assert "Direct identity fields are prohibited" in str(response.json())
        assert not TransferJob.objects.filter(
            transfer_key="site-a__video__pii-rejected"
        ).exists()

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_registration_rejects_raw_report_text(self):
        payload = self._report_transfer_payload(
            transfer_key="site-a__report__raw-text-rejected",
            pdf_hash="hash-raw-text-rejected",
        )
        payload["resource_rows"]["raw_pdf_file"]["text"] = "Raw clinical text"

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert response.status_code == 400, response.content
        assert "Raw report text is prohibited" in str(response.json())
        assert not TransferJob.objects.filter(
            transfer_key="site-a__report__raw-text-rejected"
        ).exists()

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_registration_rejects_legacy_privacy_schema(self):
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__legacy-schema-rejected",
            video_hash="hash-legacy-schema-rejected",
        )
        payload["payload_schema_version"] = "1.0"

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert response.status_code == 400, response.content
        assert "privacy-preserving" in str(response.json())

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
                "information_source_name": "manual_annotation",
            }
        ]
        payload["resource_rows"]["video_segments"] = [
            {
                "source_node_key": self.source_node.node_key,
                "source_segment_id": 17,
                "video_hash": "hash-annotations",
                "start_frame_number": 4,
                "end_frame_number_exclusive": 8,
                "label_name": "lesion_visible",
                "source_kind": "manual_annotation",
                "validation_state": "validated",
                "export_segment": True,
                "anonymous_provenance": {
                    "information_source_name": "manual_annotation"
                },
            }
        ]
        payload["resource_rows"]["video_state"]["segment_annotations_validated"] = True
        payload["resource_rows"]["reports"] = [
            {
                "template_name": "star_upper_gi_main",
                "template_version": "2026.1",
                "template_hash": "template-hash",
                "status": "final",
                "version": 2,
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
        assert annotation.annotator is None
        assert annotation.value is True
        assert annotation.float_value == 0.91
        assert (
            annotation.external_annotation_id
            == f"hub_transfer:{self.source_node.node_key}:annotation:42"
        )
        assert video.state is not None
        video.state.refresh_from_db()
        assert video.state.frame_annotations_generated is True
        assert video.state.segment_annotations_validated is True

        segment = LabelVideoSegment.objects.select_related(
            "state", "source", "label"
        ).get(source_node_key=self.source_node.node_key, source_segment_id="17")
        segment_pk = segment.pk
        assert segment.video_file == video
        assert segment.start_frame_number == 4
        assert segment.end_frame_number == 8
        assert segment.label is not None
        assert segment.label.name == "lesion_visible"
        assert segment.source is not None
        assert segment.source.name == "manual_annotation"
        assert segment.state.annotation is True
        assert segment.state.prediction is False
        assert segment.state.is_validated is True

        replay_payload = self._video_transfer_payload(
            transfer_key="site-a__video__annotations-replay",
            video_hash="hash-annotations",
        )
        replay_payload["resource_rows"]["video_segments"] = [
            {
                **payload["resource_rows"]["video_segments"][0],
                "start_frame_number": 5,
                "end_frame_number_exclusive": 9,
                "validation_state": "unvalidated",
            }
        ]
        replay_payload["resource_rows"]["video_state"][
            "segment_annotations_validated"
        ] = False
        replay_response = self._secure_post(
            "/api/media/hub/transfers/",
            data=replay_payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )
        assert replay_response.status_code == 201, replay_response.content
        assert (
            LabelVideoSegment.objects.filter(
                source_node_key=self.source_node.node_key,
                source_segment_id="17",
            ).count()
            == 1
        )
        segment.refresh_from_db()
        assert segment.pk == segment_pk
        assert segment.start_frame_number == 5
        assert segment.end_frame_number == 9
        segment.state.refresh_from_db()
        assert segment.state.is_validated is False
        video.state.refresh_from_db()
        assert video.state.segment_annotations_validated is False

        transfer_job = TransferJob.objects.get(transfer_key=payload["transfer_key"])
        report = PatientExaminationReport.objects.get(
            patient_examination_id=transfer_job.linked_patient_examination_id,
            template_name="star_upper_gi_main",
            version=2,
        )
        assert report.status == PatientExaminationReport.Status.FINAL
        assert report.title == ""
        assert report.editor_payload == {}
        assert report.rendered_text == ""

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_video_transfer_rejects_segment_beyond_transferred_frame_count(self):
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__segment-out-of-bounds",
            video_hash="hash-segment-out-of-bounds",
        )
        payload["resource_rows"]["video_segments"] = [
            {
                "source_node_key": self.source_node.node_key,
                "source_segment_id": "outside",
                "video_hash": "hash-segment-out-of-bounds",
                "start_frame_number": 299,
                "end_frame_number_exclusive": 301,
                "label_name": "lesion_visible",
                "source_kind": "manual_annotation",
                "validation_state": "validated",
                "export_segment": True,
                "anonymous_provenance": {
                    "information_source_name": "manual_annotation"
                },
            }
        ]

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert response.status_code == 400, response.content
        assert "exceeds video frame_count" in str(response.json())
        assert not VideoFile.objects.filter(
            video_hash="hash-segment-out-of-bounds"
        ).exists()
        assert not LabelVideoSegment.objects.filter(
            source_node_key=self.source_node.node_key,
            source_segment_id="outside",
        ).exists()

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_video_transfer_rolls_back_segments_before_validation_reconciliation(
        self,
    ):
        video_state = VideoState.objects.create()
        video = VideoFile.objects.create(
            video_hash="hash-segment-rollback",
            center=self.center,
            state=video_state,
        )
        assert video.state is not None
        assert video.state.segment_annotations_validated is False
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__segment-rollback",
            video_hash=video.video_hash,
        )
        payload["resource_rows"]["video_segments"] = [
            {
                "source_node_key": self.source_node.node_key,
                "source_segment_id": "rollback",
                "video_hash": video.video_hash,
                "start_frame_number": 4,
                "end_frame_number_exclusive": 8,
                "label_name": "lesion_visible",
                "source_kind": "manual_annotation",
                "validation_state": "validated",
                "export_segment": True,
                "anonymous_provenance": {
                    "information_source_name": "manual_annotation"
                },
            }
        ]
        payload["resource_rows"]["video_state"]["segment_annotations_validated"] = True

        with patch(
            "endoreg_db.services.hub.transfers._apply_frame_annotation_rows",
            side_effect=ValueError("frame annotation apply failed"),
        ):
            response = self._secure_post(
                "/api/media/hub/transfers/",
                data=payload,
                content_type="application/json",
                headers=self._auth_headers(),
            )

        assert response.status_code == 400, response.content
        assert "frame annotation apply failed" in str(response.json())
        assert not TransferJob.objects.filter(
            transfer_key=payload["transfer_key"]
        ).exists()
        assert not LabelVideoSegment.objects.filter(
            source_node_key=self.source_node.node_key,
            source_segment_id="rollback",
        ).exists()
        video.state.refresh_from_db()
        assert video.state.segment_annotations_validated is False

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
    def test_node_authenticated_transfer_ignores_missing_user_center_scope(
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

        assert response.status_code == 201, response.content
        assert TransferJob.objects.filter(
            transfer_key=payload["transfer_key"],
            source_center=self.center,
        ).exists()

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
    def test_transfer_registration_rejects_changed_canonical_replay_payload(self):
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__changed-replay",
            video_hash="hash-changed-replay",
        )

        first = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )
        payload["resource_rows"]["video_file"]["fps"] = 30.0
        second = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert first.status_code == 201, first.content
        assert second.status_code == 409, second.content
        assert "different transfer payload" in str(second.json())
        video = VideoFile.objects.get(video_hash="hash-changed-replay")
        assert video.fps == 25.0
        assert (
            TransferJob.objects.filter(transfer_key=payload["transfer_key"]).count()
            == 1
        )

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_registration_rejects_source_center_outside_node_ownership(
        self,
    ):
        other_center = Center.objects.create(
            name="other-transfer-center",
            display_name="Other Transfer Center",
        )
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__foreign-center",
            video_hash="hash-foreign-center",
        )
        payload["source_center_key"] = other_center.center_key

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )

        assert response.status_code == 400, response.content
        assert "must match the authenticated source node owning center" in str(
            response.json()
        )
        assert not TransferJob.objects.filter(
            transfer_key=payload["transfer_key"]
        ).exists()

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_registration_rejects_source_node_without_owning_center(self):
        unowned_node = NetworkNode.objects.create(
            display_name="Unowned Site Node",
            node_key="unowned-site-node",
            role=NetworkNode.Role.SITE_NODE,
        )
        unowned_secret = "unowned-site-secret"
        unowned_node.set_shared_secret(unowned_secret)
        unowned_node.save(update_fields=["shared_secret_hash"])
        payload = self._video_transfer_payload(
            transfer_key="unowned__video__center-claim",
            video_hash="hash-unowned-center-claim",
        )
        payload["source_node_key"] = unowned_node.node_key

        response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers={
                "X-Network-Node-Key": unowned_node.node_key,
                "X-Network-Node-Secret": unowned_secret,
                "X-Client-Cert-Verified": "SUCCESS",
            },
        )

        assert response.status_code == 400, response.content
        assert "must have an owning center" in str(response.json())
        assert not TransferJob.objects.filter(
            transfer_key=payload["transfer_key"]
        ).exists()

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_cross_node_hash_collision_is_persistently_inconsistent_and_immutable(
        self,
    ):
        first_payload = self._video_transfer_payload(
            transfer_key="site-a__video__owned-hash",
            video_hash="owned-hash",
        )
        first_response = self._secure_post(
            "/api/media/hub/transfers/",
            data=first_payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )
        assert first_response.status_code == 201, first_response.content

        video = VideoFile.objects.get(video_hash="owned-hash")
        video.fps = 12.0
        video.save(update_fields=["fps"])

        second_node = NetworkNode.objects.create(
            display_name="Site A Secondary Node",
            node_key="site-a-secondary-node",
            role=NetworkNode.Role.SITE_NODE,
            owning_center=self.center,
        )
        second_secret = "site-a-secondary-secret"
        second_node.set_shared_secret(second_secret)
        second_node.save(update_fields=["shared_secret_hash"])
        processed_content = b"second-node-processed-video"
        processed_hash = self._sha256(processed_content)
        second_payload = self._video_transfer_payload(
            transfer_key="site-a-secondary__video__owned-hash",
            video_hash="owned-hash",
            transfer_mode="metadata_and_processed_media",
            processed_video_hash=processed_hash,
        )
        second_payload["source_node_key"] = second_node.node_key
        second_headers = {
            "X-Network-Node-Key": second_node.node_key,
            "X-Network-Node-Secret": second_secret,
            "X-Client-Cert-Verified": "SUCCESS",
        }

        second_response = self._secure_post(
            "/api/media/hub/transfers/",
            data=second_payload,
            content_type="application/json",
            headers=second_headers,
        )

        assert second_response.status_code == 201, second_response.content
        assert second_response.json()["transfer_status"] == "inconsistent"
        assert second_response.json()["processing_decision"] == "mark_inconsistent"
        collision_job = TransferJob.objects.get(
            transfer_key=second_payload["transfer_key"]
        )
        assert collision_job.target_object_id is None
        video.refresh_from_db()
        assert video.center == self.center
        assert video.fps == 12.0

        upload_response = self._secure_post(
            f"/api/media/hub/transfers/{second_payload['transfer_key']}/media/",
            data=self._enveloped_upload_data(
                transfer_key=cast(str, second_payload["transfer_key"]),
                plaintext=processed_content,
                filename="processed.bin",
            ),
            headers=second_headers,
        )
        assert upload_response.status_code == 400, upload_response.content
        assert "inconsistent or rejected transfer" in str(upload_response.json())

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_cross_node_report_hash_collision_does_not_overwrite_report(self):
        first_payload = self._report_transfer_payload(
            transfer_key="site-a__report__owned-hash",
            pdf_hash="owned-report-hash",
        )
        first_response = self._secure_post(
            "/api/media/hub/transfers/",
            data=first_payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )
        assert first_response.status_code == 201, first_response.content

        report = RawPdfFile.objects.get(pdf_hash="owned-report-hash")
        report.anonymized_text = "Receiver-preserved anonymized report"
        report.save(update_fields=["anonymized_text"])

        second_node = NetworkNode.objects.create(
            display_name="Site A Report Node",
            node_key="site-a-report-node",
            role=NetworkNode.Role.SITE_NODE,
            owning_center=self.center,
        )
        second_secret = "site-a-report-secret"
        second_node.set_shared_secret(second_secret)
        second_node.save(update_fields=["shared_secret_hash"])
        second_payload = self._report_transfer_payload(
            transfer_key="site-a-report__report__owned-hash",
            pdf_hash="owned-report-hash",
        )
        second_payload["source_node_key"] = second_node.node_key

        second_response = self._secure_post(
            "/api/media/hub/transfers/",
            data=second_payload,
            content_type="application/json",
            headers={
                "X-Network-Node-Key": second_node.node_key,
                "X-Network-Node-Secret": second_secret,
                "X-Client-Cert-Verified": "SUCCESS",
            },
        )

        assert second_response.status_code == 201, second_response.content
        assert second_response.json()["transfer_status"] == "inconsistent"
        collision_job = TransferJob.objects.get(
            transfer_key=second_payload["transfer_key"]
        )
        assert collision_job.target_object_id is None
        report.refresh_from_db()
        assert report.center == self.center
        assert report.anonymized_text == "Receiver-preserved anonymized report"

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
    def test_report_transfer_imports_safe_final_report_rows(self):
        payload = self._report_transfer_payload(
            transfer_key="site-a__report__lx-report",
            pdf_hash="hash-lx-report",
        )
        payload["resource_rows"]["reports"] = [
            {
                "template_name": "star_colonoscopy_main",
                "template_version": "2026.1",
                "template_hash": "report-template-hash",
                "status": "final",
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
        assert report.status == PatientExaminationReport.Status.FINAL
        assert report.title == ""
        assert report.editor_payload == {}
        assert report.rendered_text == ""

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
        assert "only allowed for anonymized data that was explicitly validated" in str(
            response.json()
        )

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
                    "envelope": "{}",
                    "ciphertext": raw_bytes,
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

    @override_settings(
        ENDOREG_DEPLOYMENT_ROLE="central_hub",
        ENDOREG_HUB_TRANSFER_MAX_UPLOAD_BYTES=4,
    )
    def test_processed_video_upload_enforces_configured_size_limit(self):
        processed_bytes = b"processed-video"
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__oversized-upload",
            video_hash="oversized-video-hash",
            transfer_mode="metadata_and_processed_media",
            sender_processing_success=True,
            processed_video_hash=self._sha256(processed_bytes),
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
            data=self._enveloped_upload_data(
                transfer_key=cast(str, payload["transfer_key"]),
                plaintext=processed_bytes,
                filename="ciphertext.bin",
            ),
            headers=self._auth_headers(),
        )

        assert upload_response.status_code == 400, upload_response.content
        assert "configured size limit" in str(upload_response.json())

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_media_upload_rejects_multipart_before_parsing(self):
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

        assert upload_response.status_code == 415, upload_response.content
        assert "multipart/form-data" in str(upload_response.json())

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_media_upload_rejects_oversized_envelope_header(self):
        processed_bytes = b"processed-video"
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__oversized-envelope-header",
            video_hash="hash-oversized-envelope-header",
            transfer_mode="metadata_and_processed_media",
            sender_processing_success=True,
            processed_video_hash=self._sha256(processed_bytes),
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
            data=processed_bytes,
            content_type="application/octet-stream",
            headers={
                **self._auth_headers(),
                "X-Hub-Media-Role": "processed",
                "X-Hub-Media-Envelope": "x" * (4 * 1024 + 1),
            },
        )

        assert upload_response.status_code == 400, upload_response.content
        assert "envelope" in upload_response.json()

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_media_upload_returns_typed_model_validation_details(self):
        processed_bytes = b"processed-video"
        processed_hash = self._sha256(processed_bytes)
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__media-model-validation",
            video_hash="hash-media-model-validation",
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

        with patch(
            "endoreg_db.views.media.hub.transfers.attach_enveloped_transfer_media",
            side_effect=DjangoValidationError(
                {"processed_file": ["Processed artifact state is inconsistent."]}
            ),
        ):
            upload_response = self._secure_post(
                f"/api/media/hub/transfers/{payload['transfer_key']}/media/",
                data=self._enveloped_upload_data(
                    transfer_key=cast(str, payload["transfer_key"]),
                    plaintext=processed_bytes,
                    filename="processed.bin",
                ),
                headers=self._auth_headers(),
            )

        assert upload_response.status_code == 400, upload_response.content
        assert upload_response.json() == {
            "error": "Media attachment validation failed",
            "details": {
                "processed_file": ["Processed artifact state is inconsistent."]
            },
        }

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_media_upload_returns_conflict_for_live_operation_lease(self):
        from endoreg_db.services.hub.transfers import TransferOperationBusy

        processed_bytes = b"processed-video"
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__live-transfer-lease",
            video_hash="hash-live-transfer-lease",
            transfer_mode="metadata_and_processed_media",
            sender_processing_success=True,
            processed_video_hash=self._sha256(processed_bytes),
        )
        create_response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )
        assert create_response.status_code == 201, create_response.content

        with patch(
            "endoreg_db.views.media.hub.transfers.attach_enveloped_transfer_media",
            side_effect=TransferOperationBusy(
                "transfer operation already has a live owner"
            ),
        ):
            upload_response = self._secure_post(
                f"/api/media/hub/transfers/{payload['transfer_key']}/media/",
                data=self._enveloped_upload_data(
                    transfer_key=cast(str, payload["transfer_key"]),
                    plaintext=processed_bytes,
                    filename="processed.bin",
                ),
                headers=self._auth_headers(),
            )

        assert upload_response.status_code == 409, upload_response.content
        assert upload_response.json()["detail"] == (
            "Transfer media attachment is already in progress."
        )

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_media_upload_returns_and_logs_safe_envelope_rejection(self):
        processed_bytes = b"processed-video"
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__envelope-rejection",
            video_hash="hash-envelope-rejection",
            transfer_mode="metadata_and_processed_media",
            sender_processing_success=True,
            processed_video_hash=self._sha256(processed_bytes),
        )
        create_response = self._secure_post(
            "/api/media/hub/transfers/",
            data=payload,
            content_type="application/json",
            headers=self._auth_headers(),
        )
        assert create_response.status_code == 201, create_response.content

        with (
            patch(
                "endoreg_db.views.media.hub.transfers.attach_enveloped_transfer_media",
                side_effect=HubMediaEnvelopeError(
                    "sensitive receiver detail",
                    rejection_code="recipient_key_unavailable",
                    rejection_phase="recipient_key_unwrap",
                ),
            ),
            self.assertLogs(
                "endoreg_db.views.media.hub.transfers", level="WARNING"
            ) as transfer_logs,
        ):
            upload_response = self._secure_post(
                f"/api/media/hub/transfers/{payload['transfer_key']}/media/",
                data=self._enveloped_upload_data(
                    transfer_key=cast(str, payload["transfer_key"]),
                    plaintext=processed_bytes,
                    filename="processed.bin",
                ),
                headers=self._auth_headers(),
            )

        assert upload_response.status_code == 400, upload_response.content
        assert upload_response.json() == {
            "detail": "Encrypted media envelope validation failed.",
            "error_fields": ["envelope"],
            "rejection_code": "recipient_key_unavailable",
            "rejection_phase": "recipient_key_unwrap",
        }
        event = self._structured_event(transfer_logs.records[-1])
        assert event["event"] == "hub.transfer_media_upload_validation_failed"
        assert event["error_fields"] == ["envelope"]
        assert event["rejection_code"] == "recipient_key_unavailable"
        assert event["rejection_phase"] == "recipient_key_unwrap"
        assert "sensitive receiver detail" not in "\n".join(transfer_logs.output)

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
            data=self._enveloped_upload_data(
                transfer_key=cast(str, payload["transfer_key"]),
                plaintext=processed_bytes,
                filename="processed.bin",
            ),
            headers=self._auth_headers(),
        )

        assert upload_response.status_code == 200, upload_response.content
        body = upload_response.json()
        assert body["transfer_status"] == "applied"
        assert body["processing_decision"] == "skip_processing_preserved_state"
        assert body["transfer_key"] == payload["transfer_key"]
        assert body["resource_hash"] == payload["resource_hash"]
        assert body["processed_media_hash"] == processed_hash
        receipt = cast(dict[str, object], body["envelope_receipt"])
        assert receipt["verified"] is True
        assert receipt["plaintext_sha256"] == processed_hash
        assert receipt["target_node_key"] == self.target_node.node_key

        video = VideoFile.objects.get(video_hash=raw_hash)
        assert video.processed_video_hash == processed_hash
        assert ProcessingHistory.objects.get(file_hash=raw_hash).success is True

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_processed_upload_rejects_plaintext_without_envelope(self) -> None:
        processed_bytes = b"plaintext-must-not-cross-the-boundary"
        processed_hash = self._sha256(processed_bytes)
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__plaintext-rejected",
            video_hash=self._sha256(b"raw-plaintext-rejected"),
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
            data=processed_bytes,
            content_type="application/octet-stream",
            headers={
                **self._auth_headers(),
                "X-Hub-Media-Role": "processed",
            },
        )

        assert upload_response.status_code == 400, upload_response.content
        assert "plaintext uploads are prohibited" in str(upload_response.json())
        transfer_job = TransferJob.objects.get(transfer_key=payload["transfer_key"])
        assert transfer_job.transfer_status == TransferJob.TransferStatus.AWAITING_MEDIA

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_exact_replay_is_idempotent_and_changed_replay_is_conflict(self) -> None:
        processed_bytes = b"replay-stable-processed-video"
        processed_hash = self._sha256(processed_bytes)
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__envelope-replay",
            video_hash=self._sha256(b"raw-replay-video"),
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
        upload_data = self._enveloped_upload_data(
            transfer_key=cast(str, payload["transfer_key"]),
            plaintext=processed_bytes,
            filename="ciphertext.bin",
        )
        envelope_json = upload_data["envelope"]
        ciphertext = upload_data["ciphertext"]
        staging_directory = Path(self._key_directory.name) / "replay-staging"

        with patch(
            "endoreg_db.services.hub.transfer_envelope.TRANSCODING_DIR",
            staging_directory,
        ):
            first_response = self._secure_post(
                f"/api/media/hub/transfers/{payload['transfer_key']}/media/",
                data={
                    "media_role": "processed",
                    "envelope": envelope_json,
                    "ciphertext": ciphertext,
                },
                headers=self._auth_headers(),
            )
            assert first_response.status_code == 200, first_response.content
            video = VideoFile.objects.get(video_hash=payload["resource_hash"])
            canonical_name = str(video.processed_file.name)
            receipt = first_response.json()["envelope_receipt"]

            exact_response = self._secure_post(
                f"/api/media/hub/transfers/{payload['transfer_key']}/media/",
                data={
                    "media_role": "processed",
                    "envelope": envelope_json,
                    "ciphertext": ciphertext,
                },
                headers=self._auth_headers(),
            )
            assert exact_response.status_code == 200, exact_response.content
            assert exact_response.json()["envelope_receipt"] == receipt
            video.refresh_from_db()
            assert str(video.processed_file.name) == canonical_name

            changed_ciphertext = bytes([ciphertext[0] ^ 1]) + ciphertext[1:]
            conflict_response = self._secure_post(
                f"/api/media/hub/transfers/{payload['transfer_key']}/media/",
                data={
                    "media_role": "processed",
                    "envelope": envelope_json,
                    "ciphertext": changed_ciphertext,
                },
                headers=self._auth_headers(),
            )

        assert conflict_response.status_code == 409, conflict_response.content
        assert conflict_response.json()["transfer_status"] == "inconsistent"
        assert conflict_response.json()["processing_decision"] == "mark_inconsistent"
        video.refresh_from_db()
        assert str(video.processed_file.name) == canonical_name
        assert not staging_directory.exists() or not any(staging_directory.iterdir())

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_failed_authenticated_publication_preserves_previous_generation(
        self,
    ) -> None:
        processed_bytes = b"replacement-processed-video"
        processed_hash = self._sha256(processed_bytes)
        payload = self._video_transfer_payload(
            transfer_key="site-a__video__failed-replacement",
            video_hash=self._sha256(b"raw-failed-replacement"),
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
        video = VideoFile.objects.get(video_hash=payload["resource_hash"])
        video.processed_file.save(
            "previous-generation.mp4",
            SimpleUploadedFile("previous-generation.mp4", b"previous-generation"),
            save=True,
        )
        previous_name = str(video.processed_file.name)
        upload_data = self._enveloped_upload_data(
            transfer_key=cast(str, payload["transfer_key"]),
            plaintext=processed_bytes,
            filename="ciphertext.bin",
        )
        envelope_json = upload_data["envelope"]
        valid_ciphertext = upload_data["ciphertext"]
        tampered_ciphertext = bytes([valid_ciphertext[0] ^ 1]) + valid_ciphertext[1:]

        upload_response = self._secure_post(
            f"/api/media/hub/transfers/{payload['transfer_key']}/media/",
            data={
                "media_role": "processed",
                "envelope": envelope_json,
                "ciphertext": tampered_ciphertext,
            },
            headers=self._auth_headers(),
        )

        assert upload_response.status_code == 400, upload_response.content
        video.refresh_from_db()
        assert str(video.processed_file.name) == previous_name
        assert video.processed_file.storage.exists(previous_name)

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
                "envelope": "{}",
                "ciphertext": report_bytes,
            },
            headers=self._auth_headers(),
        )

        assert upload_response.status_code == 400, upload_response.content
        assert "Only anonymized processed media may be uploaded" in str(
            upload_response.json()
        )

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_media_upload_uses_node_scope_not_user_scope(self):
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
            data=self._enveloped_upload_data(
                transfer_key=cast(str, payload["transfer_key"]),
                plaintext=processed_bytes,
                filename="processed.bin",
            ),
            headers=self._auth_headers(),
        )

        assert upload_response.status_code == 200, upload_response.content

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_transfer_status_returns_404_for_unknown_transfer(self) -> None:
        status_response = self._secure_get(
            "/api/media/hub/transfers/does-not-exist/status/",
            headers=self._auth_headers(),
        )

        assert status_response.status_code == 404, status_response.content
        status_json = status_response.json()
        assert status_json["detail"] == "Transfer job not found"

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_media_upload_returns_404_for_unknown_transfer_key(self) -> None:
        upload_response = self._secure_post(
            "/api/media/hub/transfers/does-not-exist/media/",
            data={
                "media_role": "processed",
                "file": SimpleUploadedFile(
                    "processed.mp4",
                    b"processed-video",
                    content_type="video/mp4",
                ),
            },
            headers=self._auth_headers(),
        )

        assert upload_response.status_code == 404, upload_response.content
        assert upload_response.json()["detail"] == "Transfer job not found"

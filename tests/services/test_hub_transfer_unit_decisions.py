from __future__ import annotations

# pyright: reportPrivateUsage=false

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.models import Center, NetworkNode, TransferJob
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.services.hub import transfers
from endoreg_db.services.patient_identity.hash_aliases import record_patient_hash_alias


@pytest.mark.django_db
def test_hub_reimport_rejects_even_known_historical_patient_alias() -> None:
    center = Center.objects.create(name="hub-alias")
    patient = Patient.objects.create(
        center=center, patient_hash="a" * 64, is_real_person=False
    )
    record_patient_hash_alias(
        patient=patient, center=center, patient_hash="a" * 64, salt_fingerprint="b" * 64
    )
    Patient.objects.filter(pk=patient.pk).update(patient_hash="c" * 64)
    examination = PatientExamination.objects.create(patient=patient, hash="d" * 64)
    payload: JsonObject = {"patient_hash": "a" * 64, "examination_hash": "d" * 64}

    for patient_hash in ("a" * 64, "c" * 64):
        payload["patient_hash"] = patient_hash
        with pytest.raises(ValueError, match="must remain on the source"):
            transfers._upsert_sensitive_meta(
                existing=None, payload=payload, center=center
            )
    patient.refresh_from_db()
    examination.refresh_from_db()
    assert patient.patient_hash == "c" * 64
    assert examination.patient_id == patient.pk
    assert Patient.objects.filter(center=center).count() == 1
    assert PatientExamination.objects.filter(patient=patient).count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "conflict",
    [
        "other_center",
        "foreign_only",
        "other_patient",
        "real_patient",
        "ambiguous_alias",
    ],
)
def test_hub_identity_conflicts_roll_back(conflict: str) -> None:
    center = Center.objects.create(name="hub-conflict")
    patient = Patient.objects.create(
        center=center, patient_hash="a" * 64, is_real_person=conflict == "real_patient"
    )
    examination_patient = patient
    if conflict == "foreign_only":
        Patient.objects.filter(pk=patient.pk).update(
            center=Center.objects.create(name="hub-foreign")
        )
    if conflict in {"other_center", "other_patient"}:
        examination_patient = Patient.objects.create(
            center=Center.objects.create(name="hub-other")
            if conflict == "other_center"
            else center,
            patient_hash="a" * 64 if conflict == "other_center" else "c" * 64,
            is_real_person=False,
        )
    if conflict == "ambiguous_alias":
        record_patient_hash_alias(
            patient=patient,
            center=center,
            patient_hash="a" * 64,
            salt_fingerprint="b" * 64,
        )
        Patient.objects.filter(pk=patient.pk).update(patient_hash="c" * 64)
        Patient.objects.create(
            center=center, patient_hash="a" * 64, is_real_person=False
        )
    examination = PatientExamination.objects.create(
        patient=examination_patient, hash="d" * 64
    )
    patient_count = Patient.objects.count()
    meta_count = SensitiveMeta.objects.count()

    with pytest.raises(ValueError):
        transfers._upsert_sensitive_meta(
            existing=None,
            payload={"patient_hash": "a" * 64, "examination_hash": "d" * 64},
            center=center,
        )

    assert Patient.objects.count() == patient_count
    assert SensitiveMeta.objects.count() == meta_count
    examination.refresh_from_db()
    assert examination.patient_id == examination_patient.pk


@pytest.mark.django_db
def test_hub_rejects_patient_hash_without_creating_cross_center_identity() -> None:
    center = Center.objects.create(name="hub-local")
    other = Center.objects.create(name="hub-foreign")
    foreign_patient = Patient.objects.create(
        center=other, patient_hash="a" * 64, is_real_person=False
    )

    with pytest.raises(ValueError, match="must remain on the source"):
        transfers._upsert_sensitive_meta(
            existing=None,
            payload={"patient_hash": "a" * 64, "examination_hash": "d" * 64},
            center=center,
        )
    assert not Patient.objects.filter(center=center).exists()
    foreign_patient.refresh_from_db()
    assert foreign_patient.center_id == other.pk


@pytest.mark.django_db
@pytest.mark.parametrize(
    "conflict",
    ["center", "patient", "examination", "unlinked_patient", "unlinked_examination"],
)
def test_hub_reimport_rejects_reassignment_of_existing_metadata(conflict: str) -> None:
    center = Center.objects.create(name="hub-existing")
    payload: JsonObject = {"patient_hash": "a" * 64, "examination_hash": "d" * 64}
    patient = Patient.objects.create(
        center=center, patient_hash="a" * 64, is_real_person=False
    )
    examination = PatientExamination.objects.create(patient=patient, hash="d" * 64)
    # Represent a pre-policy imported row without invoking the now-prohibited transfer.
    meta = SensitiveMeta(
        center=center,
        patient_hash="a" * 64,
        examination_hash="d" * 64,
        pseudo_patient=patient,
        pseudo_examination=examination,
    )
    SensitiveMeta.objects.bulk_create([meta])
    if conflict.startswith("unlinked_"):
        SensitiveMeta.objects.filter(pk=meta.pk).update(
            pseudo_patient=None, pseudo_examination=None
        )
        meta.refresh_from_db()
    previous_patient_id = meta.pseudo_patient_id
    previous_examination_id = meta.pseudo_examination_id
    if conflict == "center":
        center = Center.objects.create(name="hub-other")
    elif conflict in {"patient", "unlinked_patient"}:
        payload["patient_hash"] = "b" * 64
    else:
        payload["examination_hash"] = "e" * 64

    with pytest.raises(ValueError, match="must remain on the source"):
        transfers._upsert_sensitive_meta(existing=meta, payload=payload, center=center)

    meta.refresh_from_db()
    assert meta.patient_hash == "a" * 64
    assert meta.examination_hash == "d" * 64
    assert meta.pseudo_patient_id == previous_patient_id
    assert meta.pseudo_examination_id == previous_examination_id


def _transfer_job(**overrides: object) -> TransferJob:
    values: dict[str, object] = {
        "resource_hash": "resource-hash",
        "resource_kind": TransferJob.ResourceKind.VIDEO.value,
        "transfer_mode": TransferJob.TransferMode.METADATA_ONLY.value,
        "processing_policy": TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE.value,
        "processing_snapshot": {},
        "resource_rows": {},
        "provenance": {},
        "transfer_status": TransferJob.TransferStatus.PENDING.value,
        "processing_decision": TransferJob.ProcessingDecision.WAIT_FOR_MISSING_MEDIA.value,
    }
    values.update(overrides)
    return cast(TransferJob, SimpleNamespace(**values))


class NetworkNodeAuthenticationUnitTests(TestCase):
    source_node: NetworkNode

    def setUp(self) -> None:
        center = Center.objects.create(
            name="hub-auth-unit-center",
            display_name="Hub Auth Unit Center",
        )
        self.source_node = NetworkNode.objects.create(
            display_name="Hub Auth Unit Source",
            node_key="hub-auth-unit-source",
            role=NetworkNode.Role.SITE_NODE,
            owning_center=center,
        )
        self.source_node.set_shared_secret("correct-secret")
        self.source_node.save(update_fields=["shared_secret_hash"])

    def test_unknown_source_node_records_specific_failure(self) -> None:
        with patch.object(transfers, "emit_hub_audit_event") as audit_event:
            result = transfers.authenticate_network_node(
                source_node_key="unknown-source",
                provided_node_key="unknown-source",
                provided_secret="never-log-this",
            )

        assert result is None
        assert audit_event.call_args.kwargs["reason"] == (
            "unknown_or_inactive_source_node"
        )
        assert "never-log-this" not in str(audit_event.call_args)

    def test_mismatched_node_key_records_specific_failure(self) -> None:
        with patch.object(transfers, "emit_hub_audit_event") as audit_event:
            result = transfers.authenticate_network_node(
                source_node_key=self.source_node.node_key,
                provided_node_key="different-source",
                provided_secret="never-log-this",
            )

        assert result is None
        assert audit_event.call_args.kwargs["reason"] == "node_key_mismatch"
        assert "never-log-this" not in str(audit_event.call_args)

    def test_wrong_shared_secret_records_specific_failure(self) -> None:
        with patch.object(transfers, "emit_hub_audit_event") as audit_event:
            result = transfers.authenticate_network_node(
                source_node_key=self.source_node.node_key,
                provided_node_key=self.source_node.node_key,
                provided_secret="wrong-secret",
            )

        assert result is None
        assert audit_event.call_args.kwargs["reason"] == "shared_secret_mismatch"
        assert "wrong-secret" not in str(audit_event.call_args)


@pytest.mark.parametrize(
    ("resource_rows", "message"),
    [
        (
            {"sensitive_meta": {"patient_first_name": "direct identity"}},
            "Direct identity fields are prohibited",
        ),
        (
            {"raw_pdf_file": {"text": "raw report"}},
            "Raw report text is prohibited",
        ),
        (
            {"video_file": {"original_file_name": "patient.mp4"}},
            "Unsafe video metadata fields are prohibited",
        ),
        (
            {"reports": [{"rendered_text": "unsafe report rendering"}]},
            "Unsafe structured report fields are prohibited",
        ),
    ],
    ids=[
        "direct-identity",
        "raw-report-text",
        "unsafe-video-metadata",
        "unsafe-structured-report",
    ],
)
def test_privacy_boundary_rejects_each_unsafe_payload_class(
    resource_rows: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        transfers._assert_privacy_preserving_resource_rows(
            cast(JsonObject, resource_rows)
        )


def test_privacy_boundary_rejects_pseudonymous_linkage_fields() -> None:
    with pytest.raises(ValueError, match="must remain on the source"):
        transfers._assert_privacy_preserving_resource_rows(
            {
                "sensitive_meta": {
                    "patient_hash": "patient-hash",
                    "examination_hash": "examination-hash",
                },
                "reports": [{"anonymized_text": "validated anonymous report"}],
            }
        )


@pytest.mark.parametrize(
    ("transfer_status", "processing_decision"),
    [
        (
            TransferJob.TransferStatus.INCONSISTENT.value,
            TransferJob.ProcessingDecision.WAIT_FOR_MISSING_MEDIA.value,
        ),
        (
            TransferJob.TransferStatus.PENDING.value,
            TransferJob.ProcessingDecision.REJECT_TRANSFER.value,
        ),
    ],
    ids=["inconsistent-transfer", "rejected-transfer"],
)
def test_media_upload_rejects_terminal_transfer_before_staging(
    transfer_status: str,
    processing_decision: str,
) -> None:
    transfer_job = _transfer_job(
        transfer_status=transfer_status,
        processing_decision=processing_decision,
        transfer_mode=TransferJob.TransferMode.METADATA_AND_PROCESSED_MEDIA.value,
    )

    with (
        patch("endoreg_db.utils.file_operations.atomic_handoff_file") as stage_upload,
        pytest.raises(ValueError, match="Plaintext Hub media attachment is prohibited"),
    ):
        transfers.attach_transfer_media(
            transfer_job=transfer_job,
            uploaded_file=SimpleUploadedFile("processed.mp4", b"processed"),
            media_role="processed",
        )

    stage_upload.assert_not_called()


def test_media_upload_rejects_raw_role_before_staging() -> None:
    with (
        patch("endoreg_db.utils.file_operations.atomic_handoff_file") as stage_upload,
        pytest.raises(ValueError, match="Plaintext Hub media attachment is prohibited"),
    ):
        transfers.attach_transfer_media(
            transfer_job=_transfer_job(
                transfer_mode=(
                    TransferJob.TransferMode.METADATA_AND_PROCESSED_MEDIA.value
                )
            ),
            uploaded_file=SimpleUploadedFile("raw.mp4", b"raw"),
            media_role="raw",
        )

    stage_upload.assert_not_called()


def test_media_upload_rejects_metadata_only_mode_before_staging() -> None:
    with (
        patch("endoreg_db.utils.file_operations.atomic_handoff_file") as stage_upload,
        pytest.raises(ValueError, match="Plaintext Hub media attachment is prohibited"),
    ):
        transfers.attach_transfer_media(
            transfer_job=_transfer_job(),
            uploaded_file=SimpleUploadedFile("processed.mp4", b"processed"),
            media_role="processed",
        )

    stage_upload.assert_not_called()


def test_plaintext_media_upload_rejects_unsupported_kind_before_staging() -> None:
    transfer_job = _transfer_job(
        resource_kind="unsupported",
        transfer_mode=TransferJob.TransferMode.METADATA_AND_PROCESSED_MEDIA.value,
    )
    uploaded_file = SimpleUploadedFile("processed.bin", b"processed")

    with (
        patch("endoreg_db.utils.file_operations.atomic_handoff_file") as stage_upload,
        pytest.raises(ValueError, match="Plaintext Hub media attachment is prohibited"),
    ):
        transfers.attach_transfer_media(
            transfer_job=transfer_job,
            uploaded_file=uploaded_file,
            media_role="processed",
        )

    stage_upload.assert_not_called()


@pytest.mark.parametrize(
    (
        "local_history_success",
        "local_raw_present",
        "local_processed_present",
        "transfer_mode",
        "processing_policy",
        "processing_success",
        "snapshot_success",
        "expected_decision",
        "expected_status",
    ),
    [
        (
            True,
            True,
            False,
            TransferJob.TransferMode.METADATA_ONLY.value,
            TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE.value,
            None,
            None,
            TransferJob.ProcessingDecision.SKIP_EXISTING_SUCCESS.value,
            TransferJob.TransferStatus.APPLIED.value,
        ),
        (
            False,
            False,
            False,
            TransferJob.TransferMode.METADATA_ONLY.value,
            TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE.value,
            True,
            None,
            TransferJob.ProcessingDecision.MARK_INCONSISTENT.value,
            TransferJob.TransferStatus.INCONSISTENT.value,
        ),
        (
            False,
            False,
            False,
            TransferJob.TransferMode.METADATA_ONLY.value,
            TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE.value,
            True,
            False,
            TransferJob.ProcessingDecision.WAIT_FOR_MISSING_MEDIA.value,
            TransferJob.TransferStatus.AWAITING_MEDIA.value,
        ),
        (
            False,
            False,
            False,
            TransferJob.TransferMode.METADATA_AND_PROCESSED_MEDIA.value,
            TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE.value,
            True,
            True,
            TransferJob.ProcessingDecision.WAIT_FOR_MISSING_MEDIA.value,
            TransferJob.TransferStatus.AWAITING_MEDIA.value,
        ),
    ],
    ids=[
        "existing-success",
        "preserved-success-without-artifacts",
        "snapshot-overrides-fallback",
        "processed-media-transfer-awaits-upload",
    ],
)
def test_video_processing_decision_matrix(
    local_history_success: bool,
    local_raw_present: bool,
    local_processed_present: bool,
    transfer_mode: str,
    processing_policy: str,
    processing_success: bool | None,
    snapshot_success: bool | None,
    expected_decision: str,
    expected_status: str,
) -> None:
    transfer_job = _transfer_job(
        transfer_mode=transfer_mode,
        processing_policy=processing_policy,
    )
    video = cast(
        VideoFile,
        SimpleNamespace(raw_file=object(), processed_file=object()),
    )
    processing_snapshot: JsonObject = {}
    if snapshot_success is not None:
        processing_snapshot["sender_processing_success"] = snapshot_success

    with (
        patch.object(
            transfers.ProcessingHistory,
            "has_history_for_hash",
            return_value=local_history_success,
        ),
        patch.object(
            transfers,
            "file_exists",
            side_effect=[local_raw_present, local_processed_present],
        ),
    ):
        decision, status, _detail = transfers._decide_video_processing(
            transfer_job=transfer_job,
            video=video,
            processing_success=processing_success,
            processing_snapshot=processing_snapshot,
        )

    assert decision == expected_decision
    assert status == expected_status


@pytest.mark.parametrize(
    (
        "local_history_success",
        "local_raw_present",
        "local_processed_present",
        "transfer_mode",
        "processing_policy",
        "processing_success",
        "expected_decision",
        "expected_status",
    ),
    [
        (
            True,
            False,
            True,
            TransferJob.TransferMode.METADATA_ONLY.value,
            TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE.value,
            None,
            TransferJob.ProcessingDecision.SKIP_EXISTING_SUCCESS.value,
            TransferJob.TransferStatus.APPLIED.value,
        ),
        (
            False,
            False,
            False,
            TransferJob.TransferMode.METADATA_ONLY.value,
            TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE.value,
            True,
            TransferJob.ProcessingDecision.MARK_INCONSISTENT.value,
            TransferJob.TransferStatus.INCONSISTENT.value,
        ),
        (
            False,
            False,
            False,
            TransferJob.TransferMode.METADATA_AND_PROCESSED_MEDIA.value,
            TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE.value,
            True,
            TransferJob.ProcessingDecision.WAIT_FOR_MISSING_MEDIA.value,
            TransferJob.TransferStatus.AWAITING_MEDIA.value,
        ),
    ],
    ids=[
        "existing-success",
        "preserved-success-without-artifacts",
        "processed-media-transfer-awaits-upload",
    ],
)
def test_report_processing_decision_matrix(
    local_history_success: bool,
    local_raw_present: bool,
    local_processed_present: bool,
    transfer_mode: str,
    processing_policy: str,
    processing_success: bool | None,
    expected_decision: str,
    expected_status: str,
) -> None:
    transfer_job = _transfer_job(
        resource_kind=TransferJob.ResourceKind.REPORT.value,
        transfer_mode=transfer_mode,
        processing_policy=processing_policy,
    )
    report = cast(
        RawPdfFile,
        SimpleNamespace(file=object(), processed_file=object()),
    )

    with (
        patch.object(
            transfers.ProcessingHistory,
            "has_history_for_hash",
            return_value=local_history_success,
        ),
        patch.object(
            transfers,
            "file_exists",
            side_effect=[local_raw_present, local_processed_present],
        ),
    ):
        decision, status, _detail = transfers._decide_report_processing(
            transfer_job=transfer_job,
            report=report,
            processing_success=processing_success,
        )

    assert decision == expected_decision
    assert status == expected_status


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, True),
        (False, False),
        (" YES ", True),
        ("0", False),
    ],
)
def test_optional_boolean_coercion_has_stable_wire_semantics(
    value: object,
    expected: bool | None,
) -> None:
    assert transfers._coerce_optional_bool(value) is expected


def test_apply_metadata_rejects_unknown_resource_kind() -> None:
    save = MagicMock()
    transfer_job = _transfer_job(resource_kind="unsupported", save=save)

    result = transfers.apply_transfer_metadata(transfer_job)

    assert result is transfer_job
    assert transfer_job.transfer_status == TransferJob.TransferStatus.FAILED.value
    assert (
        transfer_job.processing_decision
        == TransferJob.ProcessingDecision.REJECT_TRANSFER.value
    )
    save.assert_called_once()

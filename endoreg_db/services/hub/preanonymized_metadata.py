"""Preanonymized metadata normalization and external patient identity linking.

Callers own the publication transaction; external identity linking retains its
own atomic transaction and center row lock for direct callers. This module neither
publishes media nor owns import leases. Historical helper imports remain in
ingest.py; see structure.yml for navigation and regression tests.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from django.db import IntegrityError, transaction

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.administration.person.patient.patient_external_id import (
    PatientExternalID,
)
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.services.hub.payloads import PreanonymizedIngestPayload
from endoreg_db.utils.hashs import get_bytes_hash, get_identity_hash

# Keep the historical logger namespace for existing log filters.
logger = logging.getLogger("endoreg_db.services.hub.ingest")


@transaction.atomic
def attach_external_id_to_sensitive_meta(
    *,
    sensitive_meta: SensitiveMeta,
    external_id: str,
    external_id_origin: str,
) -> None:
    normalized_external_id = external_id.strip()
    normalized_origin = external_id_origin.strip()
    if not normalized_external_id or not normalized_origin:
        return

    # Serialize first creation within the center, including the patient and its
    # unique external mapping, so concurrent imports cannot leave orphan patients.
    center = Center.objects.select_for_update().get(pk=sensitive_meta.center_id)
    existing = PatientExternalID.objects.filter(
        origin=normalized_origin,
        external_id=normalized_external_id,
    ).first()

    if existing is None:
        pseudo_patient = sensitive_meta.pseudo_patient
        if pseudo_patient is None:
            identity = json.dumps(
                [
                    "preanonymized_external_patient_v1",
                    center.pk,
                    normalized_origin,
                    normalized_external_id,
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            patient_hash = get_identity_hash(identity)
            pseudo_patient = Patient.get_pseudo_patient_by_hash(patient_hash, center)
            if pseudo_patient is None:
                pseudo_patient = Patient.objects.create(
                    patient_hash=patient_hash,
                    center=center,
                    gender=sensitive_meta.patient_gender,
                    is_real_person=False,
                )
        if pseudo_patient.center_id != center.pk:
            raise ValueError(
                "External patient identifier belongs to a different center"
            )
        existing = PatientExternalID.objects.create(
            patient=pseudo_patient,
            origin=normalized_origin,
            external_id=normalized_external_id,
        )

    if existing.patient.center_id != center.pk:
        raise ValueError("External patient identifier belongs to a different center")

    update_fields: list[str] = []
    external_id_id = cast(int | None, getattr(sensitive_meta, "external_id_id", None))
    pseudo_patient_id = cast(
        int | None, getattr(sensitive_meta, "pseudo_patient_id", None)
    )
    if external_id_id != existing.pk:
        sensitive_meta.external_id = existing
        update_fields.append("external_id")
    canonical_patient_id = cast(int, existing.patient.pk)
    if pseudo_patient_id != canonical_patient_id:
        sensitive_meta.pseudo_patient = existing.patient
        update_fields.append("pseudo_patient")

    canonical_patient_hash = existing.patient.patient_hash
    if canonical_patient_hash and sensitive_meta.patient_hash != canonical_patient_hash:
        sensitive_meta.patient_hash = canonical_patient_hash
        update_fields.append("patient_hash")

    from endoreg_db.models.medical.patient.patient_examination import (
        PatientExamination,
    )

    examination_identity = "\0".join(
        (
            "preanonymized_external_case_v1",
            normalized_origin,
            normalized_external_id,
            sensitive_meta.casenumber or f"sensitive-meta:{sensitive_meta.pk}",
        )
    )
    canonical_examination_hash = get_bytes_hash(examination_identity.encode("utf-8"))
    canonical_examination, _ = PatientExamination.objects.get_or_create(
        hash=canonical_examination_hash,
        defaults={
            "patient": existing.patient,
            "date_start": sensitive_meta.examination_date,
        },
    )
    if canonical_examination.patient_id != canonical_patient_id:
        raise IntegrityError(
            "The deterministic preanonymized examination identity is linked "
            "to a different patient."
        )
    pseudo_examination_id = cast(
        int | None, getattr(sensitive_meta, "pseudo_examination_id", None)
    )
    if pseudo_examination_id != canonical_examination.pk:
        sensitive_meta.pseudo_examination = canonical_examination
        update_fields.append("pseudo_examination")
    if sensitive_meta.examination_hash != canonical_examination_hash:
        sensitive_meta.examination_hash = canonical_examination_hash
        update_fields.append("examination_hash")
    if update_fields:
        # SensitiveMeta.save() intentionally recalculates demographic hashes and
        # pseudo links. External-ID reconciliation is the authoritative identity
        # operation here, so persist the selected fields without re-running that
        # derivation and keep the in-memory instance aligned with the update.
        SensitiveMeta.objects.filter(pk=sensitive_meta.pk).update(
            **{
                field_name: getattr(sensitive_meta, field_name)
                for field_name in update_fields
            }
        )


def normalize_sensitive_meta_payload(
    *,
    payload: dict[str, Any],
    center: Center,
) -> dict[str, Any]:
    normalized: dict[str, Any] = {"center_name": center.name}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                continue
            normalized[key] = stripped
            continue
        normalized[key] = value

    try:
        from lx_dtypes.models.meta.SensitiveMeta import (
            SensitiveMeta as LxSensitiveMetaContract,
        )
    except Exception as exc:
        logger.debug("lx_dtypes SensitiveMeta unavailable for normalization: %s", exc)
        return normalized

    contract_input: dict[str, Any] = {}
    key_map = {
        "patient_first_name": "first_name",
        "patient_last_name": "last_name",
        "patient_dob": "dob",
        "patient_gender": "gender",
        "examination_date": "examination_date",
        "examination_time": "examination_time",
        "casenumber": "casenumber",
        "text": "text",
        "anonymized_text": "anonymized_text",
        "endoscope_type": "endoscope_type",
        "endoscope_sn": "endoscope_sn",
        "external_id": "external_id",
    }
    for source_key, target_key in key_map.items():
        value = normalized.get(source_key)
        if value is None:
            continue
        if source_key == "patient_gender" and hasattr(value, "name"):
            value = getattr(value, "name")
        contract_input[target_key] = value

    if not contract_input:
        return normalized

    validated = LxSensitiveMetaContract.model_validate(contract_input)
    contract_dump = validated.model_dump(
        include={
            "first_name",
            "last_name",
            "dob",
            "gender",
            "examination_date",
            "examination_time",
            "casenumber",
            "text",
            "anonymized_text",
            "endoscope_type",
            "endoscope_sn",
            "external_id",
        },
        exclude_none=True,
    )

    reverse_key_map = {
        "first_name": "patient_first_name",
        "last_name": "patient_last_name",
        "dob": "patient_dob",
        "gender": "patient_gender",
        "examination_date": "examination_date",
        "examination_time": "examination_time",
        "casenumber": "casenumber",
        "text": "text",
        "anonymized_text": "anonymized_text",
        "endoscope_type": "endoscope_type",
        "endoscope_sn": "endoscope_sn",
        "external_id": "external_id",
    }
    for source_key, target_key in reverse_key_map.items():
        value = contract_dump.get(source_key)
        if value is not None:
            normalized[target_key] = value

    return normalized


def apply_preanonymized_metadata(
    *,
    sensitive_meta: SensitiveMeta | None,
    center: Center,
    payload: PreanonymizedIngestPayload | None,
) -> SensitiveMeta | None:
    if not payload:
        return sensitive_meta

    payload_copy = normalize_sensitive_meta_payload(
        payload=payload.model_dump(exclude_none=True),
        center=center,
    )

    patient_hash = payload.patient_hash or ""
    examination_hash = payload.examination_hash or ""
    external_id = payload.external_id or ""
    external_id_origin = payload.external_id_origin or ""
    payload_copy.pop("external_id", None)
    payload_copy.pop("external_id_origin", None)

    if sensitive_meta is None:
        sensitive_meta = SensitiveMeta.create_from_dict(payload_copy)
    else:
        sensitive_meta = sensitive_meta.update_from_dict(payload_copy)

    if patient_hash or examination_hash:
        update_fields: list[str] = []
        if patient_hash and sensitive_meta.patient_hash != patient_hash:
            sensitive_meta.patient_hash = patient_hash
            update_fields.append("patient_hash")
        if examination_hash and sensitive_meta.examination_hash != examination_hash:
            sensitive_meta.examination_hash = examination_hash
            update_fields.append("examination_hash")
        if update_fields:
            sensitive_meta.save(update_fields=update_fields)

    if external_id and external_id_origin:
        attach_external_id_to_sensitive_meta(
            sensitive_meta=sensitive_meta,
            external_id=external_id,
            external_id_origin=external_id_origin,
        )

    return sensitive_meta

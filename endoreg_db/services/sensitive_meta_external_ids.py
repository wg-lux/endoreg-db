from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models.administration.person.patient.patient_external_id import (
        PatientExternalID,
    )
    from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta


@dataclass(frozen=True, slots=True)
class PatientExternalIdPair:
    external_id: str
    origin: str


def split_patient_external_id(
    payload: Mapping[str, object],
) -> tuple[dict[str, object], PatientExternalIdPair | None]:
    """Remove request-only external-ID fields from a generic model payload."""
    model_payload = dict(payload)
    raw_external_id = model_payload.pop("external_id", None)
    raw_origin = model_payload.pop("external_id_origin", None)
    external_id = _normalized_text(raw_external_id, field_name="external_id")
    origin = _normalized_text(raw_origin, field_name="external_id_origin")

    if external_id is None and origin is None:
        return model_payload, None
    if external_id is None or origin is None:
        raise ValueError(
            "external_id and external_id_origin must both be blank or both be provided"
        )
    return model_payload, PatientExternalIdPair(
        external_id=external_id,
        origin=origin,
    )


def assign_patient_external_id(
    *,
    sensitive_meta: SensitiveMeta,
    external_id_pair: PatientExternalIdPair,
) -> PatientExternalID:
    """Resolve or create and persist the typed external-ID relation."""
    from endoreg_db.models.administration.person.patient.patient_external_id import (
        PatientExternalID,
    )
    from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

    pseudo_patient = sensitive_meta.pseudo_patient
    if pseudo_patient is None:
        raise ValueError(
            "SensitiveMeta must have a pseudo patient before assigning an external ID"
        )

    patient_external_id, _created = PatientExternalID.objects.get_or_create(
        origin=external_id_pair.origin,
        external_id=external_id_pair.external_id,
        defaults={"patient": pseudo_patient},
    )
    sensitive_meta.external_id = patient_external_id
    if sensitive_meta.pk is None:
        raise ValueError("SensitiveMeta must be saved before assigning an external ID")
    SensitiveMeta.objects.filter(pk=sensitive_meta.pk).update(
        external_id=patient_external_id
    )
    return patient_external_id


def _normalized_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    return normalized or None


__all__ = [
    "PatientExternalIdPair",
    "assign_patient_external_id",
    "split_patient_external_id",
]

"""Center-scoped historical hash resolution; never merge clinical identities."""

from __future__ import annotations

import re

from django.db import transaction

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.administration.person.patient.patient_hash_alias import (
    PatientHashAlias,
)


def _validate_scope(
    center: Center, patient_hash: str, *, allow_opaque_current_hash: bool = False
) -> None:
    if center.pk is None or center._state.adding:
        raise ValueError("Historical identity requires a persisted center")
    if allow_opaque_current_hash:
        if not patient_hash or not patient_hash.strip():
            raise ValueError("Patient hash is required for identity resolution")
    elif re.fullmatch(r"[0-9a-f]{64}", patient_hash) is None:
        raise ValueError("Historical identity requires a canonical SHA-256 hash")


def resolve_patient_hash(
    *, center: Center, patient_hash: str, allow_opaque_current_hash: bool = False
) -> Patient | None:
    """Resolve an already-authorized center lookup, rejecting conflicting identities.

    Possession of a hash is not authentication; callers must authorize center access.
    """
    _validate_scope(
        center, patient_hash, allow_opaque_current_hash=allow_opaque_current_hash
    )
    patients = {
        row.pk: row
        for row in Patient.objects.filter(center=center, patient_hash=patient_hash)
    }
    if re.fullmatch(r"[0-9a-f]{64}", patient_hash):
        for alias in PatientHashAlias.objects.filter(
            center=center, patient_hash=patient_hash
        ).select_related("patient"):
            if alias.patient.center_id != center.pk:
                raise ValueError("Historical identity center ownership is inconsistent")
            patients[alias.patient_id] = alias.patient
    if len(patients) > 1:
        raise ValueError("Historical hash resolves to conflicting patients")
    return next(iter(patients.values()), None)


@transaction.atomic
def record_patient_hash_alias(
    *, patient: Patient, center: Center, patient_hash: str, salt_fingerprint: str
) -> PatientHashAlias:
    """Retain a validated current hash before rotation in the caller's transaction."""
    _validate_scope(center, patient_hash)
    current = Patient.objects.select_for_update().get(pk=patient.pk)
    if current.center_id != center.pk:
        raise ValueError("Historical identity belongs to a different center")
    resolved = resolve_patient_hash(center=center, patient_hash=patient_hash)
    if resolved is not None and resolved.pk != current.pk:
        raise ValueError("Historical hash belongs to a different patient")
    existing = PatientHashAlias.objects.filter(
        center=center, patient_hash=patient_hash
    ).first()
    if existing is not None:
        if (
            existing.patient_id != current.pk
            or existing.salt_fingerprint != salt_fingerprint
        ):
            raise ValueError(
                "Historical identity evidence conflicts with retained alias"
            )
        return existing
    if current.patient_hash != patient_hash:
        raise ValueError(
            "Only a patient's current hash can be retained before rotation"
        )
    alias = PatientHashAlias(
        center=center,
        patient=current,
        patient_hash=patient_hash,
        salt_fingerprint=salt_fingerprint,
    )
    alias.save()
    return alias

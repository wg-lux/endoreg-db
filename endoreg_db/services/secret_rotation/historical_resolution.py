"""Resolve returning source identities without restoring erased demographics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Q

from endoreg_db.config.identity_hashing import current_identity_keyring
from endoreg_db.services.patient_identity.hash_aliases import resolve_patient_hash
from endoreg_db.services.secret_rotation.identity import (
    IdentitySource,
    calculate_modern_patient_hash,
    identity_fingerprint,
    legacy_hash,
    require_identity_center,
    salt_fingerprint,
)
from endoreg_db.utils.hashs import get_patient_examination_hash

if TYPE_CHECKING:
    from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta


def resolve_historical_identity(
    instance: SensitiveMeta, source: IdentitySource
) -> bytes | None:
    """Reuse an authenticated erased group; new examinations use the active salt.

    The caller holds the identity rotation transaction and keyring snapshot.
    Missing historical examination dates prevent whole-group rehashing, but do
    not prevent matching a returning patient using preserved keyed evidence.
    This operation never restores identifiers or rewrites historical rows.
    """
    from endoreg_db.models.administration.person.patient.patient import Patient
    from endoreg_db.models.medical.patient.patient_examination import PatientExamination
    from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

    ring = current_identity_keyring()
    center = instance.center
    if center is None or instance.external_id is not None:
        return None
    if ring is None:
        resolved = resolve_patient_hash(
            center=center, patient_hash=str(instance.patient_hash)
        )
        if resolved is not None and resolved.patient_hash != instance.patient_hash:
            raise ValueError(
                "Historical alias requires configured generation evidence and explicit review"
            )
        return None
    require_identity_center(center)
    candidates = {
        legacy_hash(
            source.first_name, source.last_name, source.dob, center.name, None, salt
        ): salt
        for salt in ring.readers
    }
    modern_candidates = {
        calculate_modern_patient_hash(source, center.center_key, salt): salt
        for salt in ring.readers
    }
    candidates.update(modern_candidates)
    patients = list(
        Patient.objects.select_for_update().filter(patient_hash__in=candidates)
    )
    for candidate in candidates:
        resolved = resolve_patient_hash(center=center, patient_hash=candidate)
        if resolved is not None and (
            resolved.patient_hash not in candidates
            or not any(patient.pk == resolved.pk for patient in patients)
        ):
            raise ValueError(
                "Historical alias requires complete generation evidence and explicit review"
            )
    if len(patients) > 1:
        raise ValueError(
            "Salt generations resolve to different patients; explicit review required"
        )
    if not patients:
        return None
    patient = patients[0]
    if patient.center_id != center.pk:
        raise ValueError("Historical identity cannot cross center ownership")
    rows = list(
        SensitiveMeta.objects.select_for_update(of=("self",)).filter(
            Q(patient_hash=patient.patient_hash) | Q(pseudo_patient_id=patient.pk)
        )
    )
    if not any(row.direct_identifiers_cleared_at is not None for row in rows):
        return None
    salt = candidates[str(patient.patient_hash)]
    expected = identity_fingerprint(
        source,
        center.center_key
        if patient.patient_hash in modern_candidates
        else int(center.pk),
        salt,
    )
    for row in rows:
        if (
            row.center_id != center.pk
            or row.pseudo_patient_id != patient.pk
            or row.patient_hash != patient.patient_hash
            or row.external_id is not None
        ):
            raise ValueError("Historical identity links or ownership are inconsistent")
        if (
            not row.identity_fingerprint
            or row.identity_fingerprint != expected
            or row.identity_salt_fingerprint != salt_fingerprint(salt)
        ):
            raise ValueError("Historical identity evidence requires explicit review")
        if row.pseudo_examination_id is not None:
            if not PatientExamination.objects.filter(
                pk=row.pseudo_examination_id,
                patient_id=patient.pk,
                hash=row.examination_hash,
            ).exists():
                raise ValueError("Historical examination links are inconsistent")
        elif row.examination_hash:
            raise ValueError("Historical examination links are inconsistent")
    linked_examinations = set(
        PatientExamination.objects.filter(patient_id=patient.pk).values_list(
            "pk", flat=True
        )
    )
    if linked_examinations != {
        row.pseudo_examination_id
        for row in rows
        if row.pseudo_examination_id is not None
    }:
        raise ValueError(
            "Historical examination links require complete metadata evidence"
        )
    if source.examination_date is not None:
        exam_hashes = [
            legacy_hash(
                source.first_name,
                source.last_name,
                source.dob,
                center.name,
                source.examination_date,
                value,
            )
            for value in ring.readers
        ]
        exam_hashes.extend(
            get_patient_examination_hash(
                source.first_name,
                source.last_name,
                source.dob,
                examination_date=source.examination_date,
                center_key=center.center_key,
                salt=value,
            )
            for value in ring.readers
        )
        examinations = list(
            PatientExamination.objects.select_for_update(of=("self",)).filter(
                hash__in=exam_hashes
            )
        )
        if len(examinations) > 1 or any(
            exam.patient_id != patient.pk for exam in examinations
        ):
            raise ValueError("Historical examination identity requires explicit review")
        if examinations:
            instance.examination_hash = examinations[0].hash
    instance.patient_hash = patient.patient_hash
    instance.identity_fingerprint = expected
    return salt

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Protocol, cast

import pytest
from django.db import IntegrityError
from django.utils import timezone

from endoreg_db.models import (
    Center,
    Patient,
    PatientExamination,
    PatientExternalID,
    SensitiveMeta,
)
from endoreg_db.services.hub import ingest


class _AttachExternalIdentity(Protocol):
    def __call__(
        self,
        *,
        sensitive_meta: SensitiveMeta,
        external_id: str,
        external_id_origin: str,
    ) -> None: ...


_attach_external_id_to_sensitive_meta = cast(
    _AttachExternalIdentity, getattr(ingest, "_attach_external_id_to_sensitive_meta")
)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("second_origin", "second_id", "same_patient"),
    [
        ("sap:center", "2004", True),
        ("other:center", "2004", False),
        ("sap:center", "2005", False),
    ],
)
def test_external_identity_is_reused_only_in_its_source_scope(
    base_db_data: bool,
    second_origin: str,
    second_id: str,
    same_patient: bool,
) -> None:
    center = Center.objects.create(name="external-identity-center")
    first = SensitiveMeta.objects.create(center=center, casenumber="3004")
    second = SensitiveMeta.objects.create(center=center, casenumber="3004")
    before = Patient.objects.count()
    for meta, origin, external_id in (
        (first, "sap:center", "2004"),
        (second, second_origin, second_id),
    ):
        _attach_external_id_to_sensitive_meta(
            sensitive_meta=meta,
            external_id=external_id,
            external_id_origin=origin,
        )
        meta.refresh_from_db()
        patient = meta.pseudo_patient
        assert patient is not None
        assert patient.dob is None
        assert patient.first_name == patient.last_name == ""
        assert not patient.is_real_person
        assert patient.center_id == center.pk
        assert meta.patient_hash == patient.patient_hash
        assert meta.pseudo_examination is not None
        assert meta.pseudo_examination.patient_id == patient.pk
        identity = (
            meta.patient_hash,
            meta.pseudo_patient_id,
            meta.pseudo_examination_id,
        )
        meta.save()
        meta.refresh_from_db()
        assert (
            meta.patient_hash,
            meta.pseudo_patient_id,
            meta.pseudo_examination_id,
        ) == identity
    assert (first.pseudo_patient_id == second.pseudo_patient_id) is same_patient
    assert (first.pseudo_examination_id == second.pseudo_examination_id) is same_patient
    assert Patient.objects.count() == before + (1 if same_patient else 2)


@pytest.mark.django_db
def test_external_identity_rejects_cross_center_mapping(base_db_data: bool) -> None:
    first_center = Center.objects.create(name="external-first-center")
    second_center = Center.objects.create(name="external-second-center")
    patient = Patient.objects.create(center=first_center, patient_hash="existing")
    PatientExternalID.objects.create(patient=patient, origin="sap", external_id="2004")
    meta = SensitiveMeta.objects.create(center=second_center)
    with pytest.raises(ValueError, match="different center"):
        _attach_external_id_to_sensitive_meta(
            sensitive_meta=meta,
            external_id="2004",
            external_id_origin="sap",
        )
    meta.refresh_from_db()
    assert meta.pseudo_patient_id is None
    assert meta.external_id is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "field", ["patient_hash", "pseudo_patient", "examination_hash"]
)
def test_external_identity_rejects_inconsistent_links_on_save(
    base_db_data: bool,
    field: str,
) -> None:
    center = Center.objects.create(name="external-inconsistent-links")
    meta = SensitiveMeta.objects.create(center=center, casenumber="3004")
    _attach_external_id_to_sensitive_meta(
        sensitive_meta=meta, external_id="2004", external_id_origin="sap"
    )
    original = (meta.patient_hash, meta.pseudo_patient_id, meta.examination_hash)
    if field == "patient_hash":
        meta.patient_hash = "incorrect"
    elif field == "pseudo_patient":
        meta.pseudo_patient = None
    else:
        meta.examination_hash = "incorrect"
    with pytest.raises(ValueError, match="identity links are inconsistent"):
        meta.save()
    meta.refresh_from_db()
    assert (
        meta.patient_hash,
        meta.pseudo_patient_id,
        meta.examination_hash,
    ) == original


@pytest.mark.django_db
def test_external_identity_does_not_block_demographic_identity_enrollment(
    base_db_data: bool,
) -> None:
    center = Center.objects.create(name="external-before-demographics")
    meta = SensitiveMeta.objects.create(center=center)
    _attach_external_id_to_sensitive_meta(
        sensitive_meta=meta, external_id="2004", external_id_origin="sap"
    )
    demographic = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="Ada",
        patient_last_name="Lovelace",
        patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
    )
    assert demographic.identity_salt_fingerprint
    assert demographic.pseudo_patient_id is not None
    assert demographic.pseudo_patient_id != meta.pseudo_patient_id


@pytest.mark.django_db
def test_external_identity_rolls_back_patient_if_examination_conflicts(
    base_db_data: bool,
) -> None:
    center = Center.objects.create(name="external-rollback-center")
    other_patient = Patient.objects.create(center=center, patient_hash="other")
    examination_hash = hashlib.sha256(
        "\0".join(("preanonymized_external_case_v1", "sap", "2004", "3004")).encode()
    ).hexdigest()
    PatientExamination.objects.create(patient=other_patient, hash=examination_hash)
    meta = SensitiveMeta.objects.create(center=center, casenumber="3004")
    before = Patient.objects.count()
    with pytest.raises(IntegrityError, match="different patient"):
        _attach_external_id_to_sensitive_meta(
            sensitive_meta=meta,
            external_id="2004",
            external_id_origin="sap",
        )
    assert Patient.objects.count() == before
    assert not PatientExternalID.objects.filter(
        origin="sap", external_id="2004"
    ).exists()
    meta.refresh_from_db()
    assert meta.pseudo_patient_id is None

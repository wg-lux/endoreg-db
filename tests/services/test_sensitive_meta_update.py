from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

import pytest
from django.utils import timezone

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.other.gender import Gender
from endoreg_db.schemas.sensitive_meta_update import SensitiveMetaUpdateCommand
from endoreg_db.services.patient_identity.update import (
    SensitiveMetaUpdateCenterNotFoundError,
    SensitiveMetaUpdateGenderNotFoundError,
    update_sensitive_meta,
)


@pytest.fixture
def sensitive_meta() -> SensitiveMeta:
    suffix = uuid4().hex[:8]
    patient = Patient.objects.create(
        first_name="Pseudo",
        last_name="Patient",
        patient_hash=f"update-patient-{suffix}",
    )
    examination = PatientExamination.objects.create(patient=patient)
    gender = Gender.objects.create(name=f"old-gender-{suffix}")
    center = Center.objects.create(name=f"old-center-{suffix}")
    return SensitiveMeta.objects.create(
        patient_first_name="Max",
        patient_last_name="Mustermann",
        patient_dob=timezone.make_aware(datetime(1994, 3, 21)),
        examination_date=date(2025, 11, 27),
        pseudo_patient=patient,
        pseudo_examination=examination,
        patient_gender=gender,
        center=center,
    )


@pytest.mark.django_db
def test_update_sensitive_meta_applies_relations_scalars_and_explicit_false(
    sensitive_meta: SensitiveMeta,
) -> None:
    suffix = uuid4().hex[:8]
    new_center = Center.objects.create(name=f"new-center-{suffix}")
    new_gender = Gender.objects.create(name=f"new-gender-{suffix}")
    state = sensitive_meta.get_or_create_state()
    state.dob_verified = True
    state.names_verified = True
    state.save(update_fields=["dob_verified", "names_verified"])

    result = update_sensitive_meta(
        sensitive_meta_id=sensitive_meta.pk,
        command=SensitiveMetaUpdateCommand(
            patient_first_name="Anna",
            center_name=new_center.name,
            patient_gender_name=new_gender.name,
            dob_verified=False,
        ),
    )

    result.sensitive_meta.refresh_from_db()
    state.refresh_from_db()
    assert result.sensitive_meta.patient_first_name == "Anna"
    assert result.sensitive_meta.center_id == new_center.pk
    assert result.sensitive_meta.patient_gender_id == new_gender.pk
    assert state.dob_verified is False
    assert state.names_verified is False


@pytest.mark.django_db
def test_update_sensitive_meta_rejects_missing_named_relations_atomically(
    sensitive_meta: SensitiveMeta,
) -> None:
    original_first_name = sensitive_meta.patient_first_name

    with pytest.raises(
        SensitiveMetaUpdateCenterNotFoundError,
        match="Center 'missing-center' does not exist",
    ):
        update_sensitive_meta(
            sensitive_meta_id=sensitive_meta.pk,
            command=SensitiveMetaUpdateCommand(
                patient_first_name="Not persisted",
                center_name="missing-center",
            ),
        )

    sensitive_meta.refresh_from_db()
    assert sensitive_meta.patient_first_name == original_first_name

    with pytest.raises(
        SensitiveMetaUpdateGenderNotFoundError,
        match="Gender 'missing-gender' does not exist",
    ):
        update_sensitive_meta(
            sensitive_meta_id=sensitive_meta.pk,
            command=SensitiveMetaUpdateCommand(patient_gender_name="missing-gender"),
        )


@pytest.mark.django_db
@pytest.mark.parametrize("reverify", [False, True])
def test_changed_name_requires_explicit_reverification(
    sensitive_meta: SensitiveMeta, reverify: bool
) -> None:
    sensitive_meta.mark_dob_verified()
    sensitive_meta.mark_names_verified()
    command = (
        SensitiveMetaUpdateCommand(patient_first_name="Anna", names_verified=True)
        if reverify
        else SensitiveMetaUpdateCommand(patient_first_name="Anna")
    )
    result = update_sensitive_meta(sensitive_meta_id=sensitive_meta.pk, command=command)
    assert result.sensitive_meta.state_safe.names_verified is reverify
    assert result.sensitive_meta.state_safe.dob_verified


@pytest.mark.django_db
def test_failed_verification_rolls_back_identity_update(
    sensitive_meta: SensitiveMeta, monkeypatch: pytest.MonkeyPatch
) -> None:
    from endoreg_db.services.patient_identity import verification
    from endoreg_db.schemas.sensitive_meta_verification import (
        SensitiveMetaVerificationCommand,
    )
    from endoreg_db.services.patient_identity.verification import (
        SensitiveMetaVerificationResult,
    )

    sensitive_meta.mark_names_verified()
    original_name = sensitive_meta.patient_first_name
    original = verification.update_sensitive_meta_verification

    def fail_after_write(
        *, sensitive_meta_id: int, command: SensitiveMetaVerificationCommand
    ) -> SensitiveMetaVerificationResult:
        original(sensitive_meta_id=sensitive_meta_id, command=command)
        raise ValueError("verification persistence rejected")

    monkeypatch.setattr(
        verification, "update_sensitive_meta_verification", fail_after_write
    )
    with pytest.raises(ValueError, match="persistence rejected"):
        update_sensitive_meta(
            sensitive_meta_id=sensitive_meta.pk,
            command=SensitiveMetaUpdateCommand(patient_first_name="Anna"),
        )
    sensitive_meta.refresh_from_db()
    assert sensitive_meta.patient_first_name == original_name
    assert sensitive_meta.state_safe.names_verified

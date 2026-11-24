from __future__ import annotations

from datetime import date, datetime, time
from uuid import uuid4

import pytest
from django.utils import timezone

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.other.gender import Gender
from endoreg_db.schemas.sensitive_meta_verification import (
    SensitiveMetaVerificationCommand,
)
from endoreg_db.services.patient_identity.verification import (
    update_sensitive_meta_verification,
)


def _create_sensitive_meta() -> SensitiveMeta:
    suffix = uuid4().hex[:8]
    patient = Patient.objects.create(
        first_name="Pseudo",
        last_name="Patient",
        patient_hash=f"verification-patient-{suffix}",
    )
    examination = PatientExamination.objects.create(patient=patient)
    return SensitiveMeta.objects.create(
        patient_first_name="Max",
        patient_last_name="Mustermann",
        patient_dob=timezone.make_aware(datetime(1994, 3, 21, 0, 0)),
        examination_date=date(2025, 11, 27),
        examination_time=time(9, 30),
        casenumber=f"VERIFY-{suffix}",
        pseudo_patient=patient,
        pseudo_examination=examination,
        patient_gender=Gender.objects.create(name=f"verification-gender-{suffix}"),
        center=Center.objects.create(name=f"verification-center-{suffix}"),
    )


@pytest.mark.django_db
def test_update_sensitive_meta_verification_preserves_omitted_flag() -> None:
    sensitive_meta = _create_sensitive_meta()

    state = sensitive_meta.state_safe
    state.dob_verified = True
    state.names_verified = True
    state.save()

    result = update_sensitive_meta_verification(
        sensitive_meta_id=sensitive_meta.pk,
        command=SensitiveMetaVerificationCommand(names_verified=False),
    )

    state.refresh_from_db()
    assert state.dob_verified is True
    assert state.names_verified is False
    assert result.dob_verified is True
    assert result.names_verified is False
    assert result.is_verified is False


@pytest.mark.django_db
def test_update_sensitive_meta_verification_recreates_missing_state() -> None:
    sensitive_meta = _create_sensitive_meta()
    sensitive_meta.state_safe.delete()

    result = update_sensitive_meta_verification(
        sensitive_meta_id=sensitive_meta.pk,
        command=SensitiveMetaVerificationCommand(dob_verified=True),
    )

    sensitive_meta.refresh_from_db()
    assert sensitive_meta.state_safe.dob_verified is True
    assert sensitive_meta.state_safe.names_verified is False
    assert result.is_verified is False


@pytest.mark.django_db
def test_missing_state_has_one_false_verification_result() -> None:
    meta = _create_sensitive_meta()
    meta.state_safe.delete()
    meta.refresh_from_db()
    assert meta.is_verified is False
    assert meta.state_verified is False


@pytest.mark.django_db
def test_stale_model_facades_preserve_other_verification_flag() -> None:
    meta = _create_sensitive_meta()
    first = SensitiveMeta.objects.get(pk=meta.pk).state_safe
    stale = SensitiveMeta.objects.get(pk=meta.pk).state_safe
    first.mark_dob_verified()
    stale.mark_names_verified()
    stale.refresh_from_db()
    assert stale.is_verified
    meta.refresh_from_db()
    assert meta.is_verified is meta.state_verified is True


@pytest.mark.django_db
def test_unpersisted_verification_state_is_rejected() -> None:
    from endoreg_db.models.state.sensitive_meta import SensitiveMetaState

    with pytest.raises(ValueError, match="persisted"):
        SensitiveMetaState().mark_names_verified()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "field", ["patient_first_name", "patient_last_name", "patient_dob"]
)
@pytest.mark.parametrize("changed", [False, True])
def test_model_save_revokes_only_changed_identity_check(
    field: str, changed: bool
) -> None:
    meta = _create_sensitive_meta()
    update_sensitive_meta_verification(
        sensitive_meta_id=meta.pk,
        command=SensitiveMetaVerificationCommand(
            dob_verified=True, names_verified=True
        ),
    )
    if changed:
        value = (
            timezone.make_aware(datetime(1990, 1, 2))
            if field == "patient_dob"
            else "Changed"
        )
        setattr(meta, field, value)
    meta.save()
    meta.refresh_from_db()
    assert meta.state_safe.dob_verified is (not changed or field != "patient_dob")
    assert meta.state_safe.names_verified is (not changed or field == "patient_dob")


@pytest.mark.django_db
def test_partial_save_does_not_revoke_unsaved_name_change() -> None:
    meta = _create_sensitive_meta()
    meta.mark_dob_verified()
    meta.mark_names_verified()
    original_name = meta.patient_first_name
    meta.patient_first_name = "Unsaved"
    meta.validation_comment = "Reviewed"
    meta.save(update_fields=["validation_comment"])
    meta.refresh_from_db()
    assert meta.patient_first_name == original_name
    assert meta.is_verified


@pytest.mark.django_db
def test_audited_erasure_preserves_verification() -> None:
    meta = _create_sensitive_meta()
    meta.mark_dob_verified()
    meta.mark_names_verified()
    meta.create_anonymized_record()
    meta.save()
    meta.refresh_from_db()
    assert meta.direct_identifiers_cleared_at is not None
    assert meta.is_verified


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("missing_state", [False, True])
def test_parallel_verification_updates_preserve_both_flags(missing_state: bool) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import connection, connections

    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row-lock verification")
    meta = _create_sensitive_meta()
    if missing_state:
        meta.state_safe.delete()
    barrier = Barrier(2)

    def verify(dob: bool) -> None:
        connections.close_all()
        try:
            stale_meta = SensitiveMeta.objects.get(pk=meta.pk)
            barrier.wait(timeout=20)
            stale = stale_meta.get_or_create_state()
            if dob:
                stale.mark_dob_verified()
            else:
                stale.mark_names_verified()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(verify, dob) for dob in (False, True)]
        for future in futures:
            future.result(timeout=30)
    meta.refresh_from_db()
    assert meta.is_verified

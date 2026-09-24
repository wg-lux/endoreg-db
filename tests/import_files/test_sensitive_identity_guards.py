from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

import pytest
from django.test import override_settings
from django.utils import timezone
from lx_dtypes.models import SensitiveMeta as LxSensitiveMeta

from endoreg_db.import_files.file_storage.sensitive_meta_storage import (
    persist_sensitive_meta_candidate,
)
from endoreg_db.models import Center, Patient, RawPdfFile, SensitiveMeta


@pytest.mark.django_db
@pytest.mark.parametrize("wrong_salt", [False, True])
def test_legacy_salt_enrollment_preserves_identity_or_fails(
    base_db_data: bool,
    wrong_salt: bool,
) -> None:
    center = Center.objects.create(name=f"legacy-salt-{uuid4().hex}")
    first = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="Ada",
        patient_last_name="Lovelace",
        patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
        examination_date=date(2026, 9, 21),
    )
    identity = (first.patient_hash, first.examination_hash, first.pseudo_patient_id)
    SensitiveMeta.objects.filter(pk=first.pk).update(
        identity_fingerprint="",
        identity_salt_fingerprint="",
    )
    first.refresh_from_db()
    if wrong_salt:
        with override_settings(DJANGO_SALT="different-test-salt"):
            with pytest.raises(ValueError, match="does not match legacy"):
                first.save()
    else:
        first.save()
        assert first.identity_fingerprint
        assert first.identity_salt_fingerprint
    first.refresh_from_db()
    assert (
        first.patient_hash,
        first.examination_hash,
        first.pseudo_patient_id,
    ) == identity


@pytest.mark.django_db
def test_erased_legacy_identity_requires_explicit_enrollment_review(
    base_db_data: bool,
) -> None:
    center = Center.objects.create(name=f"legacy-erased-{uuid4().hex}")
    first = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="Ada",
        patient_last_name="Lovelace",
        patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
        examination_date=date(2026, 9, 21),
    )
    first.create_anonymized_record()
    SensitiveMeta.objects.filter(pk=first.pk).update(
        identity_fingerprint="",
        identity_salt_fingerprint="",
    )
    with pytest.raises(ValueError, match="requires explicit review"):
        SensitiveMeta.objects.create(
            center=center,
            patient_first_name="Ada",
            patient_last_name="Lovelace",
            patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
            examination_date=date(2026, 9, 21),
        )


@pytest.mark.django_db
def test_empty_extraction_has_no_invented_identity(base_db_data: bool) -> None:
    center = Center.objects.create(name=f"unresolved-{uuid4().hex}")
    report = RawPdfFile.objects.create(center=center, pdf_hash=uuid4().hex)
    before = Patient.objects.count()
    meta = persist_sensitive_meta_candidate(
        instance=report, candidate=LxSensitiveMeta()
    )
    meta.refresh_from_db()
    assert meta.patient_dob is None
    assert meta.examination_date is None
    assert meta.patient_hash is None
    assert meta.examination_hash is None
    assert meta.pseudo_patient_id is None
    assert meta.pseudo_examination_id is None
    assert Patient.objects.count() == before


@pytest.mark.django_db
@pytest.mark.parametrize("erased", [False, True])
def test_legacy_name_concatenation_collision_fails_without_partial_writes(
    base_db_data: bool,
    erased: bool,
) -> None:
    center = Center.objects.create(name=f"collision-{uuid4().hex}")
    first = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="AB",
        patient_last_name="C",
        patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
        examination_date=date(2026, 9, 21),
    )
    patient_id = first.pseudo_patient_id
    if erased:
        first.create_anonymized_record()
    before = SensitiveMeta.objects.count()
    with pytest.raises(ValueError, match="Ambiguous legacy"):
        SensitiveMeta.objects.create(
            center=center,
            patient_first_name="A",
            patient_last_name="BC",
            patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
            examination_date=date(2026, 9, 21),
        )
    assert SensitiveMeta.objects.count() == before
    first.refresh_from_db()
    assert first.pseudo_patient_id == patient_id


@pytest.mark.django_db
def test_erased_identity_reuse_and_salt_drift_are_verified(base_db_data: bool) -> None:
    center = Center.objects.create(name=f"salt-drift-{uuid4().hex}")
    first = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="Ada",
        patient_last_name="Lovelace",
        patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
        examination_date=date(2026, 9, 21),
    )
    patient_id = first.pseudo_patient_id
    first.create_anonymized_record()
    second = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="Ada",
        patient_last_name="Lovelace",
        patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
        examination_date=date(2026, 9, 21),
    )
    assert second.pseudo_patient_id == patient_id
    with override_settings(DJANGO_SALT="different-test-salt"):
        with pytest.raises(ValueError, match="salt differs"):
            SensitiveMeta.objects.create(
                center=center,
                patient_first_name="Other",
                patient_last_name="Person",
                patient_dob=timezone.make_aware(datetime(1981, 1, 2)),
                examination_date=date(2026, 9, 21),
            )


@pytest.mark.django_db
def test_missing_examination_date_never_creates_an_examination(
    base_db_data: bool,
) -> None:
    center = Center.objects.create(name=f"no-exam-{uuid4().hex}")
    meta = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="Ada",
        patient_last_name="Lovelace",
        patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
    )
    assert meta.patient_hash
    assert meta.pseudo_patient_id is not None
    assert meta.examination_date is None
    assert meta.examination_hash is None
    assert meta.pseudo_examination_id is None


@pytest.mark.django_db
@pytest.mark.parametrize("verified", [False, True])
def test_provisional_legacy_hash_is_not_global_salt_evidence(
    base_db_data: bool,
    verified: bool,
) -> None:
    center = Center.objects.create(name=f"provisional-{uuid4().hex}")
    old = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="Legacy",
        patient_last_name="Source",
        patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
        examination_date=date(2026, 9, 21),
    )
    links = (old.patient_hash, old.pseudo_patient_id, old.pseudo_examination_id)
    # Reproduce old imports whose OCR fields changed after provisional hashing.
    SensitiveMeta.objects.filter(pk=old.pk).update(
        patient_first_name="Unverified OCR",
        identity_fingerprint="",
        identity_salt_fingerprint="",
    )
    state = old.get_or_create_state()
    state.names_verified = state.dob_verified = verified
    state.save()

    def import_new() -> SensitiveMeta:
        return SensitiveMeta.objects.create(
            center=center,
            patient_first_name="New",
            patient_last_name="Patient",
            patient_dob=timezone.make_aware(datetime(1990, 2, 3)),
        )

    if verified:
        with pytest.raises(ValueError, match="does not match legacy"):
            import_new()
    else:
        new = import_new()
        assert new.patient_hash
        assert new.pseudo_patient_id != old.pseudo_patient_id
    old.refresh_from_db()
    assert (old.patient_hash, old.pseudo_patient_id, old.pseudo_examination_id) == links


@pytest.mark.django_db
def test_every_verified_legacy_identity_is_checked(base_db_data: bool) -> None:
    center = Center.objects.create(name=f"all-evidence-{uuid4().hex}")
    rows = [
        SensitiveMeta.objects.create(
            center=center,
            patient_first_name=name,
            patient_last_name="Patient",
            patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
        )
        for name in ("First", "Second")
    ]
    for row in rows:
        state = row.get_or_create_state()
        state.names_verified = state.dob_verified = True
        state.save()
    SensitiveMeta.objects.filter(pk__in=[row.pk for row in rows]).update(
        identity_fingerprint="",
        identity_salt_fingerprint="",
    )
    SensitiveMeta.objects.filter(pk=rows[1].pk).update(patient_first_name="Changed")
    with pytest.raises(ValueError, match="does not match legacy"):
        SensitiveMeta.objects.create(
            center=center,
            patient_first_name="Third",
            patient_last_name="Patient",
            patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
        )

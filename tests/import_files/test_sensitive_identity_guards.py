from __future__ import annotations

import secrets
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from django.test import override_settings
from django.utils import timezone
from lx_dtypes.models import SensitiveMeta as LxSensitiveMeta

from endoreg_db.import_files.file_storage.sensitive_meta_storage import (
    persist_sensitive_meta_candidate,
)
from endoreg_db.models import (
    Center,
    Patient,
    PatientExamination,
    RawPdfFile,
    SensitiveMeta,
)
from endoreg_db.services.privacy.quality_evaluation import DIRECT_IDENTIFIER_FIELDS
from endoreg_db.services.secret_rotation.identity import (
    IdentitySource,
    identity_fingerprint,
    legacy_hash,
    salt_fingerprint,
)
from endoreg_db.utils.hashs import get_identity_salt
from tests.services.test_secret_rotation import write_ring


@pytest.mark.django_db
def test_validation_erasure_is_complete_idempotent_and_preserves_identity(
    base_db_data: bool,
) -> None:
    center = Center.objects.create(name=f"erasure-{uuid4().hex}")
    meta = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="Ada",
        patient_last_name="Lovelace",
        patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
        examination_date=date(2026, 9, 21),
        casenumber="private-case",
        examiner_first_name="Private",
        examiner_last_name="Examiner",
        text="Private source text",
        anonymized_text="Unverified residual text",
        file_path="private-source.pdf",
        endoscope_sn="private-serial",
        validation_comment="Private comment",
    )
    identity = (
        meta.patient_hash,
        meta.examination_hash,
        meta.pseudo_patient_id,
        meta.pseudo_examination_id,
        meta.identity_fingerprint,
    )
    meta.create_anonymized_record()
    assert all(getattr(meta, name) in (None, "") for name in DIRECT_IDENTIFIER_FIELDS)
    assert not meta.examiners.exists()
    receipt = meta.direct_identifier_tombstone
    assert meta.direct_identifiers_cleared_at is not None
    assert receipt["pseudonym_hashes_retained"] is True
    meta.create_anonymized_record()
    meta.save()
    meta.refresh_from_db()
    assert meta.direct_identifier_tombstone == receipt
    assert not meta.examiners.exists()
    assert (
        meta.patient_hash,
        meta.examination_hash,
        meta.pseudo_patient_id,
        meta.pseudo_examination_id,
        meta.identity_fingerprint,
    ) == identity
    meta.patient_first_name = "Reintroduced"
    with pytest.raises(ValueError, match="cannot contain direct identifiers"):
        meta.save()
    meta.refresh_from_db()
    assert meta.patient_first_name is None


@pytest.mark.django_db
def test_erasure_rejects_identity_changed_since_validation(base_db_data: bool) -> None:
    center = Center.objects.create(name=f"changed-before-erasure-{uuid4().hex}")
    meta = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="Ada",
        patient_last_name="Lovelace",
        patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
        examination_date=date(2026, 9, 21),
    )
    SensitiveMeta.objects.filter(pk=meta.pk).update(patient_hash="f" * 64)
    with pytest.raises(ValueError, match="Identity changed"):
        meta.create_anonymized_record()
    meta.refresh_from_db()
    assert meta.patient_first_name == "Ada"
    assert meta.direct_identifiers_cleared_at is None


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
        with override_settings(DJANGO_SALT=secrets.token_hex(32)):
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
    with pytest.raises(ValueError, match="explicit review"):
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    # Modern HMAC input is unambiguous. Enroll actual legacy persisted values
    # rather than expecting modern hashes of AB/C and A/BC to collide.
    old_salt = secrets.token_hex(32).encode()
    source = IdentitySource(first_name="AB", last_name="C", dob=date(1980, 1, 2))
    historical_hash = legacy_hash("AB", "C", source.dob, center.name, None, old_salt)
    historical_exam = legacy_hash(
        "AB", "C", source.dob, center.name, date(2026, 9, 21), old_salt
    )
    Patient.objects.filter(pk=patient_id).update(patient_hash=historical_hash)
    PatientExamination.objects.filter(pk=first.pseudo_examination_id).update(
        hash=historical_exam
    )
    SensitiveMeta.objects.filter(pk=first.pk).update(
        patient_hash=historical_hash,
        examination_hash=historical_exam,
        identity_fingerprint=identity_fingerprint(source, center.pk, old_salt),
        identity_salt_fingerprint=salt_fingerprint(old_salt),
    )
    ring = write_ring(
        tmp_path, get_identity_salt().encode(), (old_salt,), kind="identity"
    )
    monkeypatch.setenv("DJANGO_IDENTITY_SALT_KEYRING_FILE", str(ring))
    before = (
        SensitiveMeta.objects.count(),
        Patient.objects.count(),
        PatientExamination.objects.count(),
    )
    message = "Historical identity evidence" if erased else "Identity collision"
    with pytest.raises(ValueError, match=message):
        SensitiveMeta.objects.create(
            center=center,
            patient_first_name="A",
            patient_last_name="BC",
            patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
            examination_date=date(2026, 9, 21),
        )
    assert (
        SensitiveMeta.objects.count(),
        Patient.objects.count(),
        PatientExamination.objects.count(),
    ) == before
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
    with override_settings(DJANGO_SALT=secrets.token_hex(32)):
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

"""Center linkage, persistence, and concurrency contracts for local UUIDv8."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import close_old_connections
from django.test import override_settings

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.other.gender import Gender
from endoreg_db.services.patient_identity.hash_aliases import record_patient_hash_alias
from endoreg_db.utils.hashs import get_hub_record_pseudonym, get_patient_uuid
from tests.services.test_hub_pseudonyms import SALT_A, SALT_B


def test_published_derivation_vectors() -> None:
    assert get_patient_uuid("a" * 64, center_key="center-a", salt=SALT_A) == UUID(
        "b6f25a1c-b530-8a77-8ab5-0f139f4bb040"
    )
    assert get_hub_record_pseudonym(
        UUID("1b4e28ba-2fa1-41d2-883f-0016d3cca427"),
        kind="patient",
        hub_node_key="hub-a",
        salt=SALT_A,
    ) == UUID("29fd5491-7550-8fe8-852b-5aab05739048")


@pytest.mark.parametrize("patient_hash", ["a" * 64, "legacy-opaque-identity"])
def test_center_linkage_and_domain_separation(patient_hash: str) -> None:
    local = get_patient_uuid(patient_hash, center_key="center-a", salt=SALT_A)
    assert local.version == 8
    assert local.variant == "specified in RFC 4122"
    assert local == get_patient_uuid(patient_hash, center_key=" center-a ", salt=SALT_A)
    assert local != get_patient_uuid(patient_hash, center_key="center-b", salt=SALT_A)
    assert local != get_patient_uuid(patient_hash, center_key="center-a", salt=SALT_B)
    assert local != get_patient_uuid(
        patient_hash + "b", center_key="center-a", salt=SALT_A
    )
    hub = get_hub_record_pseudonym(
        local, kind="patient", hub_node_key="hub-a", salt=SALT_A
    )
    assert hub != local
    assert hub == get_hub_record_pseudonym(
        get_patient_uuid(patient_hash, center_key="center-a", salt=SALT_A),
        kind="patient",
        hub_node_key="hub-a",
        salt=SALT_A,
    )


@pytest.mark.parametrize(
    "patient_hash,center", [("", "a"), (" \n", "a"), ("a", ""), ("a", " ")]
)
def test_invalid_scope_is_rejected(patient_hash: str, center: str) -> None:
    with pytest.raises(ValueError):
        get_patient_uuid(patient_hash, center_key=center, salt=SALT_A)


def test_invalid_secret_is_rejected() -> None:
    with pytest.raises(ImproperlyConfigured, match="Invalid identity salt configuration"):
        get_patient_uuid("a" * 64, center_key="center-a", salt="")


@pytest.mark.django_db
def test_new_import_uuid_and_existing_uuid_survive_rotation() -> None:
    center = Center.objects.create(name="uuid-center", center_key="center-a")
    gender = Gender.objects.create(name="uuid-gender")
    with override_settings(DJANGO_SALT=SALT_A):
        patient, created = Patient.get_or_create_pseudo_patient_by_hash(
            "a" * 64, center, gender, 3, 1980
        )
        expected = get_patient_uuid("a" * 64, center_key="center-a")
        assert created and patient.uuid == expected
        record_patient_hash_alias(
            patient=patient,
            center=center,
            patient_hash="a" * 64,
            salt_fingerprint="b" * 64,
        )
    patient.patient_hash = "c" * 64
    patient.save(update_fields=["patient_hash"])
    with override_settings(DJANGO_SALT=SALT_B):
        for patient_hash in ("a" * 64, "c" * 64):
            again, created = Patient.get_or_create_pseudo_patient_by_hash(
                patient_hash, center
            )
            assert not created and again.uuid == expected
    patient.refresh_from_db()
    assert patient.uuid == expected


@pytest.mark.django_db
def test_existing_random_uuid_is_preserved() -> None:
    center = Center.objects.create(name="old-uuid-center")
    original = uuid4()
    patient = Patient.objects.create(
        uuid=original, patient_hash="a" * 64, center=center, is_real_person=False
    )
    again, created = Patient.get_or_create_pseudo_patient_by_hash("a" * 64, center)
    assert not created and again.pk == patient.pk and again.uuid == original


@pytest.mark.django_db(transaction=True)
def test_concurrent_creation_reuses_one_patient() -> None:
    center = Center.objects.create(name="concurrent-uuid-center")
    gender = Gender.objects.create(name="concurrent-uuid-gender")
    barrier = Barrier(2)

    def create() -> tuple[int, UUID, bool]:
        close_old_connections()
        try:
            barrier.wait(timeout=15)
            patient, created = Patient.get_or_create_pseudo_patient_by_hash(
                "d" * 64, center, gender, 3, 1980
            )
            return patient.pk, patient.uuid, created
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert results[0][:2] == results[1][:2]
    assert sorted(result[2] for result in results) == [False, True]
    assert Patient.objects.filter(center=center, patient_hash="d" * 64).count() == 1

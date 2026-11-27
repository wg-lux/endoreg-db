"""Hub record identity derivation, conflict decisions and their locked caller."""

from __future__ import annotations

# pyright: reportPrivateUsage=false

import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from django.db import close_old_connections, connection

from endoreg_db.models import Center, NetworkNode
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.services.hub import transfers
from endoreg_db.services.hub.hub_record_identity import (
    HubRecordIdentityHashes,
    assert_examination_matches_identity,
    assert_existing_meta_matches_identity,
    assert_patient_owned_by_source,
    derive_hub_record_identity_hashes,
)

PATIENT_UUID = UUID("821d81f1-fb20-8e38-b777-01c67fe03001")
EXAMINATION_UUID = UUID("821d81f1-fb20-8e38-b777-01c67fe03002")
HASHES = HubRecordIdentityHashes(patient_hash="a" * 64, examination_hash="d" * 64)


def _patient(
    *, pk: int = 1, center_id: int = 10, patient_hash: str = "a" * 64
) -> Patient:
    return Patient(pk=pk, center_id=center_id, patient_hash=patient_hash)


def _existing(**overrides: object) -> SensitiveMeta:
    values: dict[str, object] = {
        "pk": 5,
        "center_id": 10,
        "patient_hash": "a" * 64,
        "examination_hash": "d" * 64,
        "pseudo_patient_id": 1,
        "pseudo_examination_id": 7,
    }
    values.update(overrides)
    return SensitiveMeta(**values)


def _no_previous_patient(patient_hash: str) -> Patient | None:
    raise AssertionError(f"resolver must not run for {patient_hash[:4]}")


def test_namespace_hashes_remain_stable() -> None:
    hashes = derive_hub_record_identity_hashes(
        source_node_pk=3,
        center_pk=4,
        patient_uuid=PATIENT_UUID,
        examination_uuid=EXAMINATION_UUID,
    )

    # Persisted hub rows depend on this exact namespace format.
    assert (
        hashes.patient_hash
        == hashlib.sha256(
            f"hub-record-v5:3:4:patient:{PATIENT_UUID}".encode()
        ).hexdigest()
    )
    assert (
        hashes.examination_hash
        == hashlib.sha256(
            f"hub-record-v5:3:4:examination:{EXAMINATION_UUID}".encode()
        ).hexdigest()
    )


@pytest.mark.parametrize(("source_node_pk", "center_pk"), [(4, 4), (3, 5), (5, 3)])
def test_namespace_hashes_bind_source_node_and_center(
    source_node_pk: int, center_pk: int
) -> None:
    reference = derive_hub_record_identity_hashes(
        source_node_pk=3,
        center_pk=4,
        patient_uuid=PATIENT_UUID,
        examination_uuid=EXAMINATION_UUID,
    )

    other = derive_hub_record_identity_hashes(
        source_node_pk=source_node_pk,
        center_pk=center_pk,
        patient_uuid=PATIENT_UUID,
        examination_uuid=EXAMINATION_UUID,
    )

    assert other.patient_hash != reference.patient_hash
    assert other.examination_hash != reference.examination_hash


@pytest.mark.parametrize("patient", [None, _patient()], ids=["unknown", "same_source"])
def test_patient_owned_by_source_accepts(patient: Patient | None) -> None:
    assert_patient_owned_by_source(patient=patient, center_pk=10, patient_hash="a" * 64)


@pytest.mark.parametrize(
    "patient",
    [_patient(center_id=11), _patient(patient_hash="b" * 64)],
    ids=["other_center", "other_source_hash"],
)
def test_patient_owned_by_source_rejects(patient: Patient) -> None:
    with pytest.raises(ValueError, match="different source owner"):
        assert_patient_owned_by_source(
            patient=patient, center_pk=10, patient_hash="a" * 64
        )


@pytest.mark.parametrize(
    ("existing", "patient"),
    [
        (_existing(), _patient()),
        (_existing(pseudo_patient_id=None, patient_hash="", examination_hash=""), None),
    ],
    ids=["linked_retry", "unlinked_without_hashes"],
)
def test_existing_meta_accepts_without_resolving_previous_patient(
    existing: SensitiveMeta, patient: Patient | None
) -> None:
    assert_existing_meta_matches_identity(
        existing=existing,
        center_pk=10,
        patient=patient,
        hashes=HASHES,
        resolve_previous_patient=_no_previous_patient,
    )


def test_existing_meta_accepts_previous_hash_resolving_to_same_patient() -> None:
    patient = _patient()
    resolved: list[str] = []

    def resolve(patient_hash: str) -> Patient | None:
        resolved.append(patient_hash)
        return patient

    assert_existing_meta_matches_identity(
        existing=_existing(patient_hash="c" * 64),
        center_pk=10,
        patient=patient,
        hashes=HASHES,
        resolve_previous_patient=resolve,
    )

    assert resolved == ["c" * 64]


@pytest.mark.parametrize(
    ("existing", "patient", "previous", "message"),
    [
        (
            _existing(center_id=11, patient_hash="c" * 64),
            _patient(),
            None,
            "different center",
        ),
        (
            _existing(pseudo_patient_id=2),
            _patient(),
            None,
            "linked to a different patient",
        ),
        (_existing(pseudo_patient_id=1), None, None, "linked to a different patient"),
        (
            _existing(pseudo_patient_id=None, patient_hash="c" * 64),
            _patient(),
            _patient(pk=2),
            "different patient identity",
        ),
        (
            _existing(pseudo_patient_id=None, patient_hash="c" * 64),
            None,
            None,
            "different patient identity",
        ),
        (
            _existing(examination_hash="e" * 64),
            _patient(),
            None,
            "different examination identity",
        ),
    ],
    ids=[
        "center_before_resolver",
        "other_pseudo_patient",
        "pseudo_patient_without_patient",
        "previous_hash_other_patient",
        "previous_hash_without_patient",
        "other_examination_hash",
    ],
)
def test_existing_meta_rejects_reassignment(
    existing: SensitiveMeta,
    patient: Patient | None,
    previous: Patient | None,
    message: str,
) -> None:
    def resolve(_patient_hash: str) -> Patient | None:
        if existing.center_id != 10:
            raise AssertionError("center conflict must be reported first")
        return previous

    with pytest.raises(ValueError, match=message):
        assert_existing_meta_matches_identity(
            existing=existing,
            center_pk=10,
            patient=patient,
            hashes=HASHES,
            resolve_previous_patient=resolve,
        )


def _examination(
    *, pk: int = 7, patient_id: int = 1, hash: str = "d" * 64
) -> PatientExamination:
    return PatientExamination(pk=pk, patient_id=patient_id, hash=hash)


@pytest.mark.parametrize(
    ("patient_examination", "existing", "patient"),
    [
        (None, None, None),
        (None, _existing(pseudo_examination_id=None), _patient()),
        (_examination(), None, _patient()),
        (_examination(), _existing(), _patient()),
    ],
    ids=["new", "existing_unlinked", "known", "known_linked"],
)
def test_examination_matches_identity_accepts(
    patient_examination: PatientExamination | None,
    existing: SensitiveMeta | None,
    patient: Patient | None,
) -> None:
    assert_examination_matches_identity(
        patient_examination=patient_examination,
        existing=existing,
        patient=patient,
        examination_hash="d" * 64,
    )


@pytest.mark.parametrize(
    ("patient_examination", "existing", "patient", "message"),
    [
        (_examination(), None, None, "different patient_hash"),
        (_examination(patient_id=2), None, _patient(), "different patient_hash"),
        (
            _examination(hash="e" * 64),
            _existing(pseudo_examination_id=8),
            _patient(),
            "different patient_hash",
        ),
        (None, _existing(), _patient(), "linked to a different examination"),
        (
            _examination(pk=8),
            _existing(),
            _patient(),
            "linked to a different examination",
        ),
    ],
    ids=[
        "known_without_patient",
        "other_patient",
        "other_hash_reported_first",
        "linked_examination_missing",
        "linked_examination_other",
    ],
)
def test_examination_matches_identity_rejects(
    patient_examination: PatientExamination | None,
    existing: SensitiveMeta | None,
    patient: Patient | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        assert_examination_matches_identity(
            patient_examination=patient_examination,
            existing=existing,
            patient=patient,
            examination_hash="d" * 64,
        )


def _source_nodes(center: Center, count: int) -> list[NetworkNode]:
    return [
        NetworkNode.objects.create(
            node_key=f"identity-site-{index}",
            display_name=f"Identity site {index}",
            owning_center=center,
            role=NetworkNode.Role.SITE_NODE,
        )
        for index in range(count)
    ]


def _concurrent_upserts(
    center: Center, nodes: list[NetworkNode], examination_uuids: list[str]
) -> list[str]:
    barrier = Barrier(len(nodes))

    def upsert(index: int) -> str:
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            transfers._upsert_sensitive_meta(
                existing=None,
                payload={
                    "patient_uuid": str(PATIENT_UUID),
                    "examination_uuid": examination_uuids[index],
                },
                center=center,
                source_node=nodes[index],
            )
            return "linked"
        except ValueError as exc:
            return str(exc)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=len(nodes)) as executor:
        return list(executor.map(upsert, range(len(nodes))))


@pytest.mark.django_db(transaction=True, available_apps=["endoreg_db"])
def test_postgresql_concurrent_sources_cannot_share_patient_uuid() -> None:
    """The center row lock lets exactly one source namespace claim a UUID."""

    if connection.vendor != "postgresql":
        pytest.skip("concurrent identity claims are verified against PostgreSQL")
    center = Center.objects.create(name="hub-identity-race")
    nodes = _source_nodes(center, 2)

    outcomes = _concurrent_upserts(
        center,
        nodes,
        [str(EXAMINATION_UUID), "821d81f1-fb20-8e38-b777-01c67fe03003"],
    )

    assert sorted(outcomes) == [
        "Hub patient UUID belongs to a different source owner.",
        "linked",
    ]
    assert Patient.objects.filter(uuid=PATIENT_UUID).count() == 1
    assert PatientExamination.objects.count() == 1
    assert SensitiveMeta.objects.count() == 1


@pytest.mark.django_db(transaction=True, available_apps=["endoreg_db"])
def test_postgresql_concurrent_same_source_retries_share_identity() -> None:
    """Concurrent retries from one source create one patient and examination."""

    if connection.vendor != "postgresql":
        pytest.skip("concurrent identity claims are verified against PostgreSQL")
    center = Center.objects.create(name="hub-identity-retry-race")
    node = _source_nodes(center, 1)[0]

    outcomes = _concurrent_upserts(
        center, [node, node], [str(EXAMINATION_UUID), str(EXAMINATION_UUID)]
    )

    assert outcomes == ["linked", "linked"]
    assert Patient.objects.filter(uuid=PATIENT_UUID).count() == 1
    assert PatientExamination.objects.filter(uuid=EXAMINATION_UUID).count() == 1

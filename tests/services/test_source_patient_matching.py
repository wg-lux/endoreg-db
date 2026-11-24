"""Real source-generated identity hashes must not enter central patient matching."""

from datetime import date
import secrets

import pytest
from django.test import override_settings

from endoreg_db.models import Center, Patient, PatientExamination
from endoreg_db.services.hub import transfers
from endoreg_db.utils import hashs


@pytest.mark.django_db
@pytest.mark.parametrize("different_source_center", [False, True])
@pytest.mark.parametrize("different_source_secret", [False, True])
@pytest.mark.parametrize("different_examination", [False, True])
def test_source_hashes_are_rejected_without_central_salt(
    monkeypatch: pytest.MonkeyPatch,
    different_source_center: bool,
    different_source_secret: bool,
    different_examination: bool,
) -> None:
    center = Center.objects.create(name="source-local-matching")
    second_center = (
        Center.objects.create(name="other-source-local-matching")
        if different_source_center
        else center
    )
    source_secret = secrets.token_hex(32)
    second_secret = secrets.token_hex(32) if different_source_secret else source_secret
    birthday = date(1980, 1, 2)
    first_examination = date(2026, 1, 1)
    second_examination = (
        date(2026, 2, 1) if different_examination else first_examination
    )

    # Source phase: use the real application hashing contract and local secrets.
    payloads = [
        {
            "patient_hash": hashs.get_patient_identity_fingerprint(
                "Synthetic", "Person", birthday, source_center.center_key, salt=secret
            ),
            "examination_hash": hashs.get_patient_examination_hash(
                "Synthetic",
                "Person",
                birthday,
                center_key=source_center.center_key,
                examination_date=examination_date,
                salt=secret,
            ),
        }
        for source_center, secret, examination_date in (
            (center, source_secret, first_examination),
            (second_center, second_secret, second_examination),
        )
    ]

    def reject_salt_access(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("Central matching must not access an identity salt")

    # Receiver phase: even an attempted salt resolution fails the test.
    monkeypatch.setattr(hashs, "_get_active_salt_bytes", reject_salt_access)
    with override_settings(DJANGO_SALT=None):
        for payload in (*payloads, payloads[1]):
            with pytest.raises(ValueError, match="must remain on the source"):
                transfers._upsert_sensitive_meta(  # pyright: ignore[reportPrivateUsage]
                    existing=None, payload=dict(payload), center=center
                )

    assert (payloads[0]["patient_hash"] != payloads[1]["patient_hash"]) is (
        different_source_secret or different_source_center
    )
    assert (payloads[0]["examination_hash"] != payloads[1]["examination_hash"]) is (
        different_source_secret or different_source_center or different_examination
    )
    assert not Patient.objects.filter(center=center).exists()
    assert not PatientExamination.objects.filter(patient__center=center).exists()


@pytest.mark.django_db
def test_uuids_link_retries_and_reject_source_ownership_collisions_without_salts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from endoreg_db.models import NetworkNode

    center = Center.objects.create(name="record-id-transfer")
    nodes = [
        NetworkNode.objects.create(
            node_key=f"record-site-{index}",
            display_name=f"Record site {index}",
            owning_center=center,
            role=NetworkNode.Role.SITE_NODE,
        )
        for index in range(2)
    ]

    def reject_salt_access(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("Receiver must not use source or central salts")

    monkeypatch.setattr(hashs, "_get_active_salt_bytes", reject_salt_access)
    with override_settings(DJANGO_SALT=None):
        first = transfers._upsert_sensitive_meta(  # pyright: ignore[reportPrivateUsage]
            existing=None,
            payload={
                "patient_uuid": "821d81f1-fb20-8e38-b777-01c67fe03001",
                "examination_uuid": "821d81f1-fb20-8e38-b777-01c67fe03002",
            },
            center=center,
            source_node=nodes[0],
        )
        retry = transfers._upsert_sensitive_meta(  # pyright: ignore[reportPrivateUsage]
            existing=first,
            payload={
                "patient_uuid": "821d81f1-fb20-8e38-b777-01c67fe03001",
                "examination_uuid": "821d81f1-fb20-8e38-b777-01c67fe03002",
            },
            center=center,
            source_node=nodes[0],
        )
        second_exam = transfers._upsert_sensitive_meta(  # pyright: ignore[reportPrivateUsage]
            existing=None,
            payload={
                "patient_uuid": "821d81f1-fb20-8e38-b777-01c67fe03001",
                "examination_uuid": "821d81f1-fb20-8e38-b777-01c67fe03003",
            },
            center=center,
            source_node=nodes[0],
        )
        with pytest.raises(ValueError, match="different source owner"):
            transfers._upsert_sensitive_meta(  # pyright: ignore[reportPrivateUsage]
                existing=None,
                payload={
                    "patient_uuid": "821d81f1-fb20-8e38-b777-01c67fe03001",
                    "examination_uuid": "821d81f1-fb20-8e38-b777-01c67fe03002",
                },
                center=center,
                source_node=nodes[1],
            )
        other_node = transfers._upsert_sensitive_meta(  # pyright: ignore[reportPrivateUsage]
            existing=None,
            payload={
                "patient_uuid": "821d81f1-fb20-8e38-b777-01c67fe03005",
                "examination_uuid": "821d81f1-fb20-8e38-b777-01c67fe03006",
            },
            center=center,
            source_node=nodes[1],
        )
        with pytest.raises(ValueError, match="different patient_hash"):
            transfers._upsert_sensitive_meta(  # pyright: ignore[reportPrivateUsage]
                existing=None,
                payload={
                    "patient_uuid": "821d81f1-fb20-8e38-b777-01c67fe03004",
                    "examination_uuid": "821d81f1-fb20-8e38-b777-01c67fe03002",
                },
                center=center,
                source_node=nodes[0],
            )
    assert retry.pk == first.pk
    assert retry.pseudo_examination_id == first.pseudo_examination_id
    assert second_exam.pseudo_patient_id == first.pseudo_patient_id
    assert second_exam.pseudo_examination_id != first.pseudo_examination_id
    assert first.pseudo_patient is not None
    assert first.pseudo_examination is not None
    assert str(first.pseudo_patient.uuid) == "821d81f1-fb20-8e38-b777-01c67fe03001"
    assert str(first.pseudo_examination.uuid) == "821d81f1-fb20-8e38-b777-01c67fe03002"
    assert other_node.pseudo_patient_id != first.pseudo_patient_id
    assert other_node.pseudo_examination_id != first.pseudo_examination_id
    # UUID retries link records; independently created source records stay separate.
    assert other_node.patient_hash != first.patient_hash
    assert Patient.objects.filter(center=center).count() == 2

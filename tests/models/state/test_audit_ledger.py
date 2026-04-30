from __future__ import annotations

import pytest

from endoreg_db.models.state.audit_ledger import AuditLedger, LedgerHead


@pytest.fixture(autouse=True)
def empty_audit_ledger():
    LedgerHead.objects.all().delete()
    AuditLedger.objects.all().delete()
    yield
    LedgerHead.objects.all().delete()
    AuditLedger.objects.all().delete()


@pytest.mark.django_db
def test_audit_ledger_first_insert_initializes_head_from_zero_hash():
    entry = AuditLedger.objects.create(
        object_type="SensitiveMeta",
        object_pk="case-1",
        action="identity_committed",
        data={"patient_hash": "p1"},
    )

    head = LedgerHead.objects.get(pk=1)
    assert entry.prev_hash == "0" * 64
    assert len(entry.hash) == 64
    assert head.current_hash == entry.hash
    assert head.last_entry == entry
    assert AuditLedger.verify_chain() is True


@pytest.mark.django_db
def test_audit_ledger_second_insert_links_to_previous_hash():
    first = AuditLedger.objects.create(
        object_type="SensitiveMeta",
        object_pk="case-1",
        action="identity_committed",
        data={"patient_hash": "p1"},
    )
    second = AuditLedger.objects.create(
        object_type="SensitiveMeta",
        object_pk="case-2",
        action="identity_committed",
        data={"patient_hash": "p2"},
    )

    head = LedgerHead.objects.get(pk=1)
    assert second.prev_hash == first.hash
    assert head.current_hash == second.hash
    assert head.last_entry == second
    assert AuditLedger.verify_chain() is True


@pytest.mark.django_db
def test_audit_ledger_rows_are_immutable_after_insert():
    entry = AuditLedger.objects.create(
        object_type="SensitiveMeta",
        object_pk="case-1",
        action="identity_committed",
        data={"patient_hash": "p1"},
    )

    entry.action = "tampered"
    with pytest.raises(RuntimeError, match="immutable"):
        entry.save()


@pytest.mark.django_db
def test_verify_chain_detects_tampered_payload():
    entry = AuditLedger.objects.create(
        object_type="SensitiveMeta",
        object_pk="case-1",
        action="identity_committed",
        data={"patient_hash": "p1"},
    )

    AuditLedger.objects.filter(pk=entry.pk).update(data={"patient_hash": "changed"})

    assert AuditLedger.verify_chain() is False


@pytest.mark.django_db
def test_verify_chain_detects_diverged_ledger_head():
    AuditLedger.objects.create(
        object_type="SensitiveMeta",
        object_pk="case-1",
        action="identity_committed",
        data={"patient_hash": "p1"},
    )
    LedgerHead.objects.filter(pk=1).update(current_hash="f" * 64)

    assert AuditLedger.verify_chain() is False


@pytest.mark.django_db
def test_append_identity_commit_drops_unauthenticated_user_context():
    user = type("AnonymousUserLike", (), {"is_authenticated": False})()

    entry = AuditLedger.append_identity_commit(
        user=user,
        object_type="SensitiveMeta",
        object_pk="case-1",
        data={"patient_hash": "p1"},
    )

    assert entry is not None
    assert entry.user is None
    assert entry.action == "identity_committed"
    assert AuditLedger.verify_chain() is True

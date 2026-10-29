from __future__ import annotations

import pytest
import uuid
from datetime import timedelta
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone

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
def test_audit_ledger_canonicalizes_nested_json_on_direct_save():
    entry = AuditLedger.objects.create(
        object_type="SensitiveMeta",
        object_pk="case-1",
        action="identity_committed",
        data={"nested": {"ok": True}, "items": [1, None, "value"]},
    )

    assert entry.data == {
        "nested": {"ok": True},
        "items": [1, None, "value"],
    }
    entry.refresh_from_db()
    assert entry.data == {
        "nested": {"ok": True},
        "items": [1, None, "value"],
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    "invalid_data",
    [None, ["not-an-object"], {"bad": (1, 2)}, {"bad": float("nan")}, {1: "bad"}],
)
def test_audit_ledger_rejects_invalid_data_on_direct_save(invalid_data: object):
    with pytest.raises(ValidationError) as exc_info:
        AuditLedger.objects.create(
            object_type="SensitiveMeta",
            object_pk="case-1",
            action="identity_committed",
            data=invalid_data,
        )

    assert "data" in exc_info.value.message_dict
    assert AuditLedger.objects.count() == 0


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
def test_append_rejects_missing_head_without_restarting_existing_chain():
    AuditLedger.append_identity_commit(object_type="Test", object_pk="1", data={})
    LedgerHead.objects.all().delete()
    with pytest.raises(RuntimeError, match="head is inconsistent"):
        AuditLedger.append_identity_commit(object_type="Test", object_pk="2", data={})
    assert AuditLedger.objects.count() == 1
    assert not LedgerHead.objects.exists()


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


@pytest.mark.django_db
@pytest.mark.parametrize("backdated", [False, True])
def test_verification_uses_hash_links_not_timestamp_or_uuid_order(backdated: bool):
    timestamp = timezone.now()
    first = AuditLedger.objects.create(
        id=uuid.UUID(int=2),
        ts=timestamp,
        object_type="Test",
        object_pk="1",
        action="created",
        data={},
    )
    second = AuditLedger.objects.create(
        id=uuid.UUID(int=1),
        ts=timestamp - timedelta(days=1) if backdated else timestamp,
        object_type="Test",
        object_pk="2",
        action="created",
        data={},
    )
    assert second.prev_hash == first.hash
    assert AuditLedger.verify_chain() is True
    assert AuditLedger.verify_chain() is True


@pytest.mark.django_db
@pytest.mark.parametrize("damage", ["delete", "head_pointer", "orphan", "missing_head"])
def test_verification_rejects_incomplete_or_inconsistent_chain(damage: str):
    first = AuditLedger.append_identity_commit(
        object_type="Test", object_pk="1", data={}
    )
    second = AuditLedger.append_identity_commit(
        object_type="Test", object_pk="2", data={}
    )
    if damage == "delete":
        AuditLedger.objects.filter(pk=first.pk).delete()
    elif damage == "head_pointer":
        LedgerHead.objects.filter(pk=1).update(last_entry=first)
    elif damage == "missing_head":
        LedgerHead.objects.all().delete()
    else:
        LedgerHead.objects.filter(pk=1).update(
            current_hash=first.hash, last_entry=first
        )
    assert first.hash != second.hash
    assert AuditLedger.verify_chain() is False


@pytest.mark.django_db
@pytest.mark.parametrize("error_type", [OperationalError, ProgrammingError])
@pytest.mark.parametrize("failure_point", ["lock", "save"])
def test_audit_failure_rolls_back_enclosing_transaction(
    error_type: type[Exception],
    failure_point: str,
):
    from django.contrib.auth.models import Group

    with pytest.raises(error_type, match="ledger unavailable"):
        with transaction.atomic():
            Group.objects.create(name="audit-rollback-test")
            with patch.object(
                LedgerHead, failure_point, side_effect=error_type("ledger unavailable")
            ):
                AuditLedger.append_identity_commit(
                    object_type="Test", object_pk="1", data={}
                )
    assert not Group.objects.filter(name="audit-rollback-test").exists()
    assert not AuditLedger.objects.exists()
    assert not LedgerHead.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_equal_time_appends_have_one_complete_chain():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import close_old_connections

    timestamp = timezone.now()
    barrier = Barrier(4)

    def append(index: int) -> None:
        close_old_connections()
        try:
            barrier.wait(timeout=15)
            AuditLedger.objects.create(
                ts=timestamp,
                object_type="Test",
                object_pk=str(index),
                action="created",
                data={},
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(append, range(4)))
    assert AuditLedger.objects.count() == 4
    assert AuditLedger.verify_chain() is True

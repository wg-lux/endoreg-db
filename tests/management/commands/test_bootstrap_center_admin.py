from __future__ import annotations

from io import StringIO
from typing import Protocol, cast

import pytest
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.core.management.base import CommandError

from endoreg_db.management.commands.bootstrap_center_admin import (
    AUDIT_ACTION,
    CENTER_SCOPE_ADMIN_GROUP,
)
from endoreg_db.models.state.audit_ledger import AuditLedger


pytestmark = pytest.mark.django_db


class _UserManager(Protocol):
    def create_user(
        self,
        username: str,
        password: str | None = None,
        **extra_fields: object,
    ) -> _TestUser: ...


class _GroupRelation(Protocol):
    def add(self, *objs: Group | int) -> None: ...


class _TestUser(Protocol):
    pk: int
    username: str
    is_staff: bool
    is_superuser: bool
    groups: _GroupRelation

    def refresh_from_db(self) -> None: ...


def _existing_user(username: str = "lx_bootstrap_admin") -> _TestUser:
    return cast(_UserManager, User.objects).create_user(username=username)


def _grant_group(user: _TestUser, group: Group) -> None:
    user.groups.add(group)


def _grant_exact_bootstrap_group(user: _TestUser) -> None:
    group = Group.objects.create(name=CENTER_SCOPE_ADMIN_GROUP)
    _grant_group(user, group)


def test_bootstrap_center_admin_requires_existing_user() -> None:
    with pytest.raises(CommandError, match="must complete a Keycloak login first"):
        call_command("bootstrap_center_admin", username="missing-user")


def test_bootstrap_center_admin_requires_exact_group() -> None:
    user = _existing_user()
    _grant_group(user, Group.objects.create(name="endoregdb_user"))

    with pytest.raises(CommandError, match="exact synchronized center_scope:admin"):
        call_command("bootstrap_center_admin", username=user.username)

    user.refresh_from_db()
    assert user.is_staff is False
    assert user.is_superuser is False
    assert AuditLedger.objects.filter(action=AUDIT_ACTION).count() == 0


def test_bootstrap_center_admin_promotes_and_audits_transactionally() -> None:
    user = _existing_user()
    _grant_exact_bootstrap_group(user)
    stdout = StringIO()

    call_command("bootstrap_center_admin", username=user.username, stdout=stdout)

    user.refresh_from_db()
    assert user.is_staff is True
    assert user.is_superuser is True
    entry = AuditLedger.objects.get(
        object_type="User",
        object_pk=str(user.pk),
        action=AUDIT_ACTION,
    )
    assert entry.user is None
    assert entry.data == {
        "target_username": user.username,
        "required_group": CENTER_SCOPE_ADMIN_GROUP,
        "previous_is_staff": False,
        "previous_is_superuser": False,
        "is_staff": True,
        "is_superuser": True,
        "initiated_by": "deployment_control_plane",
    }
    assert "recorded the immutable audit entry" in stdout.getvalue()


def test_bootstrap_center_admin_is_idempotent() -> None:
    user = _existing_user()
    _grant_exact_bootstrap_group(user)
    call_command("bootstrap_center_admin", username=user.username)
    stdout = StringIO()

    call_command("bootstrap_center_admin", username=user.username, stdout=stdout)

    assert (
        AuditLedger.objects.filter(
            object_type="User",
            object_pk=str(user.pk),
            action=AUDIT_ACTION,
        ).count()
        == 1
    )
    assert "already a Django staff superuser" in stdout.getvalue()


def test_bootstrap_center_admin_rolls_back_when_audit_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _existing_user()
    _grant_exact_bootstrap_group(user)

    def skip_audit_save(
        self: AuditLedger,
        *args: object,
        **kwargs: object,
    ) -> None:
        return None

    monkeypatch.setattr(AuditLedger, "save", skip_audit_save)

    with pytest.raises(CommandError, match="promotion was rolled back"):
        call_command("bootstrap_center_admin", username=user.username)

    user.refresh_from_db()
    assert user.is_staff is False
    assert user.is_superuser is False

from __future__ import annotations

from typing import Protocol, cast, overload

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.db.models.query import QuerySet

from endoreg_db.models.state.audit_ledger import AuditLedger


CENTER_SCOPE_ADMIN_GROUP = "center_scope:admin"
AUDIT_ACTION = "center_admin_bootstrapped"
User = get_user_model()


class _UserGroups(Protocol):
    def filter(self, *, name: str) -> QuerySet[Group]: ...


class _BootstrapUser(Protocol):
    pk: int
    username: str
    is_staff: bool
    is_superuser: bool
    groups: _UserGroups

    @overload
    def save(self) -> None: ...

    @overload
    def save(self, *, update_fields: list[str]) -> None: ...


class Command(BaseCommand):
    help = (
        "Promote an existing Keycloak-provisioned user to Django staff and "
        "superuser after verifying the exact center_scope:admin group."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--username",
            required=True,
            help="Exact username of an existing Keycloak-provisioned Django user.",
        )

    def handle(self, *args: object, **options: object) -> None:
        username = str(options.get("username") or "").strip()
        if not username:
            raise CommandError("--username must not be empty")

        with transaction.atomic():
            raw_user = (
                User.objects.select_for_update().filter(username=username).first()
            )
            if raw_user is None:
                raise CommandError(
                    "User does not exist. The user must complete a Keycloak login first."
                )
            user = cast(_BootstrapUser, raw_user)

            if not user.groups.filter(name=CENTER_SCOPE_ADMIN_GROUP).exists():
                raise CommandError(
                    f"User must have the exact synchronized {CENTER_SCOPE_ADMIN_GROUP} "
                    "group before promotion."
                )

            if bool(user.is_staff) and bool(user.is_superuser):
                self.stdout.write(
                    self.style.SUCCESS(
                        f"User {username} is already a Django staff superuser; no change made."
                    )
                )
                return

            previous_is_staff = bool(user.is_staff)
            previous_is_superuser = bool(user.is_superuser)
            user.is_staff = True
            user.is_superuser = True
            user.save(update_fields=["is_staff", "is_superuser"])

            entry = AuditLedger.objects.create(
                user=None,
                object_type="User",
                object_pk=str(user.pk),
                action=AUDIT_ACTION,
                data={
                    "target_username": username,
                    "required_group": CENTER_SCOPE_ADMIN_GROUP,
                    "previous_is_staff": previous_is_staff,
                    "previous_is_superuser": previous_is_superuser,
                    "is_staff": True,
                    "is_superuser": True,
                    "initiated_by": "deployment_control_plane",
                },
            )
            entry_pk = getattr(entry, "pk", None)
            if entry_pk is None or not AuditLedger.objects.filter(pk=entry_pk).exists():
                raise CommandError(
                    "Audit ledger is unavailable; superuser promotion was rolled back."
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Promoted existing user {user.username} to Django staff "
                "superuser and recorded the immutable audit entry."
            )
        )

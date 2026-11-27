"""Persistence-only boundary for service-owned state mutations.

Services decide transitions and grant one exact write. This module makes no
lifecycle decisions and never grants authority to an ORM bulk operation.
"""

from __future__ import annotations

from collections.abc import Collection, Generator, Iterable, Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import ClassVar, Self, Unpack

from django.db import models, router, transaction
from django.db.models.base import ModelBase

from endoreg_db.helpers.typing import DjangoModelSaveKwargs


class StateWriteForbidden(ValueError):
    """A state mutation did not come from its owning service."""


@dataclass(frozen=True)
class _WritePermit:
    instance: GuardedStateModel
    primary_key: object
    using: str
    expected: dict[str, object] | None
    changes: dict[str, object]


def _field_values(instance: models.Model, fields: Iterable[str]) -> dict[str, object]:
    return {field: getattr(instance, field) for field in fields}


def _field_default(instance: models.Model, name: str) -> object:
    field = instance._meta.get_field(name)
    if not isinstance(field, models.Field):
        raise TypeError("Only concrete state fields can be guarded")
    return field.get_default()


class GuardedStateQuerySet[M: models.Model](models.QuerySet[M]):
    def _reject_state_fields(self, fields: Iterable[str]) -> None:
        if not issubclass(self.model, GuardedStateModel):
            raise TypeError("Guarded queryset requires a guarded model")
        protected = self.model.guarded_state_fields
        if protected.intersection(fields):
            raise StateWriteForbidden(
                "State fields require the owning lifecycle service"
            )

    def update(self, **kwargs: object) -> int:
        self._reject_state_fields(kwargs)
        return super().update(**kwargs)

    def bulk_update(
        self, objs: Iterable[M], fields: Iterable[str], batch_size: int | None = None
    ) -> int:
        fields = tuple(fields)
        self._reject_state_fields(fields)
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(
        self,
        objs: Iterable[M],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Collection[str] | None = None,
        unique_fields: Collection[str] | None = None,
    ) -> list[M]:
        self._reject_state_fields(update_fields or ())
        rows = list(objs)
        for row in rows:
            if not isinstance(row, GuardedStateModel):
                raise TypeError("Guarded queryset requires a guarded model")
            row.require_initial_state_defaults()
        return super().bulk_create(
            rows,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )


class GuardedStateManager[M: models.Model](
    models.Manager[M].from_queryset(GuardedStateQuerySet)
):
    pass


class GuardedStateModel(models.Model):
    # Django specializes Model.objects on each concrete subclass.
    objects: ClassVar[GuardedStateManager[Self]] = GuardedStateManager()  # pyright: ignore[reportIncompatibleVariableOverride]
    guarded_state_fields: ClassVar[frozenset[str]] = frozenset()
    state_write_permit: _WritePermit | None = None
    _base_save_permitted: bool = False

    class Meta:
        abstract = True
        base_manager_name = "objects"
        default_manager_name = "objects"

    def require_initial_state_defaults(self) -> None:
        for name in self.guarded_state_fields:
            if getattr(self, name) != _field_default(self, name):
                raise StateWriteForbidden(
                    f"Initial {name} requires the owning lifecycle service"
                )

    def require_state_write_authorization(self) -> None:
        if self.state_write_permit is None:
            raise StateWriteForbidden(
                "State methods require the owning lifecycle service"
            )

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        if args:
            raise TypeError("Guarded state saves require keyword arguments")
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        kwargs["using"] = using
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = frozenset(update_fields)
            kwargs["update_fields"] = update_fields
            if not update_fields:
                return
        written = self.guarded_state_fields
        if update_fields is not None:
            written &= update_fields
        elif self.get_deferred_fields() and self._state.db == using:
            # Match Django's implicit update_fields for deferred instances.
            written -= self.get_deferred_fields()
        permit = self.state_write_permit
        self.state_write_permit = None
        with transaction.atomic(using=using):
            current = (
                type(self)
                ._base_manager.using(using)
                .select_for_update()
                .filter(pk=self.pk)
                .values(*self.guarded_state_fields)
                .first()
            )
            if permit is not None:
                if (
                    permit.instance is not self
                    or permit.primary_key != self.pk
                    or permit.using != using
                    or permit.expected != current
                    or not permit.changes.keys() <= written
                    or _field_values(self, permit.changes) != permit.changes
                ):
                    raise StateWriteForbidden(
                        "State write authorization is stale or mismatched"
                    )
            if current is None:
                if not self._state.adding:
                    raise StateWriteForbidden("Persisted state row disappeared")
                if permit is None:
                    self.require_initial_state_defaults()
                elif any(
                    getattr(self, name) != _field_default(self, name)
                    and (
                        name not in permit.changes
                        or getattr(self, name) != permit.changes[name]
                    )
                    for name in self.guarded_state_fields
                ):
                    raise StateWriteForbidden(
                        "Initial state exceeds authorized targets"
                    )
                if not kwargs.get("force_update"):
                    # A concurrent insert must conflict, never become an UPDATE.
                    kwargs["force_insert"] = True
            else:
                changes = {
                    name: getattr(self, name)
                    for name in written
                    if getattr(self, name) != current[name]
                }
                if changes and (
                    permit is None
                    or any(
                        name not in permit.changes or permit.changes[name] != value
                        for name, value in changes.items()
                    )
                ):
                    raise StateWriteForbidden(
                        "State changes require the owning lifecycle service"
                    )
            expected_after = (
                _field_values(self, self.guarded_state_fields)
                if current is None
                else {**current, **_field_values(self, written)}
            )
            self._base_save_permitted = True
            try:
                super().save(**kwargs)
            finally:
                self._base_save_permitted = False
            persisted = (
                type(self)
                ._base_manager.using(using)
                .filter(pk=self.pk)
                .values(*self.guarded_state_fields)
                .get()
            )
            if persisted != expected_after:
                raise StateWriteForbidden(
                    "Save hooks changed protected state; write rolled back"
                )

    def save_base(
        self,
        raw: bool = False,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._base_save_permitted or raw:
            raise StateWriteForbidden("Direct save_base bypasses the state service")
        self._base_save_permitted = False
        super().save_base(
            raw=raw,
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )


@contextmanager
def authorize_state_write(
    instance: GuardedStateModel,
    *,
    expected: Mapping[str, object] | None,
    changes: Mapping[str, object],
    using: str,
) -> Generator[None]:
    """Grant one exact save after the owning service accepts a transition.

    The service holds the row lock. Save rechecks the full persisted snapshot;
    authorization cannot be transferred to another instance, alias or later state.
    """
    if not transaction.get_connection(using).in_atomic_block:
        raise StateWriteForbidden("State authorization requires an atomic block")
    if not changes or not changes.keys() <= instance.guarded_state_fields:
        raise StateWriteForbidden(
            "State authorization requires explicit protected targets"
        )
    if expected is not None and frozenset(expected) != instance.guarded_state_fields:
        raise StateWriteForbidden(
            "State authorization requires the complete prior snapshot"
        )
    if instance.state_write_permit is not None:
        raise StateWriteForbidden("Nested state authorization is forbidden")
    permit = _WritePermit(
        instance,
        instance.pk,
        using,
        deepcopy(dict(expected)) if expected is not None else None,
        deepcopy(dict(changes)),
    )
    instance.state_write_permit = permit
    try:
        yield
    finally:
        instance.state_write_permit = None

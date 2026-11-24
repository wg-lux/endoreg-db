"""Shared Django and loader typing boundaries."""

from collections.abc import Iterable
from typing import TYPE_CHECKING, Protocol, TypeAlias, TypedDict, cast

from django.core.files import File
from django.db.models import Model, QuerySet
from django.db.models.base import ModelBase
from rest_framework.permissions import (
    BasePermission,
    OperandHolder,
    SingleOperandHolder,
)

CompositePermissionClass: TypeAlias = (
    type[BasePermission] | OperandHolder | SingleOperandHolder
)


class DjangoModelSaveKwargs(TypedDict, total=False):
    """Keyword arguments accepted by ``django.db.models.Model.save``."""

    force_insert: bool | tuple[ModelBase, ...]
    force_update: bool
    using: str | None
    update_fields: Iterable[str] | None


class ManyToManyAddRelation(Protocol):
    def add(self, *objs: Model) -> None: ...


class ReferenceRelation(Protocol):
    """Shared ORM relation boundary for graph and catalogue reference projection."""

    def all(self) -> QuerySet[Model]: ...
    def set(self, _objects: Iterable[Model], /) -> None: ...


def m2m_add_relation(manager: object) -> ManyToManyAddRelation:
    return cast(ManyToManyAddRelation, manager)


if TYPE_CHECKING:
    DjangoFile: TypeAlias = File[bytes]
else:
    DjangoFile: TypeAlias = File


class BinaryFieldFileSaver(Protocol):
    """Django's binary save contract, narrowed once at its unparameterized stub."""

    def save(self, name: str, content: DjangoFile, save: bool = True) -> None: ...

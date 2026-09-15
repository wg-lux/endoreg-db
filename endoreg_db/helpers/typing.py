# endoreg_db/helpers/typing.py

from collections.abc import Iterable
from typing import Protocol, TypedDict, cast

from django.db.models import Model
from django.db.models.base import ModelBase


class DjangoModelSaveKwargs(TypedDict, total=False):
    """Keyword arguments accepted by ``django.db.models.Model.save``."""

    force_insert: bool | tuple[ModelBase, ...]
    force_update: bool
    using: str | None
    update_fields: Iterable[str] | None


class ManyToManyAddRelation(Protocol):
    def add(self, *objs: Model) -> None: ...


def m2m_add_relation(manager: object) -> ManyToManyAddRelation:
    return cast(ManyToManyAddRelation, manager)

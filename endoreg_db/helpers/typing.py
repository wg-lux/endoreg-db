# endoreg_db/helpers/typing.py

from typing import Protocol, cast

from django.db.models import Model


class ManyToManyAddRelation(Protocol):
    def add(self, *objs: Model) -> None: ...


def m2m_add_relation(manager: object) -> ManyToManyAddRelation:
    return cast(ManyToManyAddRelation, manager)

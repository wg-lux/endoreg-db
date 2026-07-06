from __future__ import annotations

from types import NoneType
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast

from django.db import models

from endoreg_db.utils import create_mock_examiner_name, get_examiner_hash

from ....utils import DJANGO_NAME_SALT
from ..person import Person

if TYPE_CHECKING:
    from ...center.center import Center
    from ...person.names.first_name import FirstName
    from ...person.names.last_name import LastName
    from ..user.portal_user_information import PortalUserInfo

NoExaminerValue: TypeAlias = NoneType
ExaminerFirstNameInput: TypeAlias = "str | FirstName"
ExaminerLastNameInput: TypeAlias = "str | LastName"


class _ExaminerNameSource(Protocol):
    name: str


class Examiner(Person):
    if TYPE_CHECKING:
        center: models.ForeignKey[Center | NoExaminerValue]

    center: models.ForeignKey[Center | NoExaminerValue | None] = models.ForeignKey(
        "Center", on_delete=models.CASCADE, blank=True, null=True
    )
    hash: "models.CharField[str]" = models.CharField(max_length=255, unique=True)

    if TYPE_CHECKING:
        portal_user_info: models.OneToOneField["PortalUserInfo"]

    def __str__(self) -> str:
        return self.first_name + " " + self.last_name

    @classmethod
    def custom_get_or_create(
        cls,
        first_name: ExaminerFirstNameInput,
        last_name: ExaminerLastNameInput,
        center: "Center",
        substitute_names: bool = True,
    ) -> tuple["Examiner", bool]:
        from ...person.names.first_name import FirstName
        from ...person.names.last_name import LastName

        if isinstance(first_name, FirstName):
            first_name = cast(_ExaminerNameSource, first_name).name

        if isinstance(last_name, LastName):
            last_name = cast(_ExaminerNameSource, last_name).name

        real_hash = get_examiner_hash(
            first_name=first_name,
            last_name=last_name,
            center_name=center.name,
            salt=DJANGO_NAME_SALT,
        )

        if substitute_names:
            name_tuple = create_mock_examiner_name()

        else:
            name_tuple = (first_name, last_name)
        defaults = dict(
            first_name=name_tuple[0],
            last_name=name_tuple[1],
            center=center,
        )
        examiner, created = cls.objects.get_or_create(hash=real_hash, defaults=defaults)
        return examiner, created

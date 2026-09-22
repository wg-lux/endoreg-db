from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeAlias, Any, cast

from django.db import models
from endoreg_db.services.secret_rotation.identity import (
    identity_rotation_transaction,
    rotate_examiner_match,
    examiner_identity_fields,
)

from endoreg_db.utils import create_mock_examiner_name, get_examiner_hash

from ..person import Person

if TYPE_CHECKING:
    from ...center.center import Center
    from ...person.names.first_name import FirstName
    from ...person.names.last_name import LastName
    from ..user.portal_user_information import PortalUserInfo

ExaminerFirstNameInput: TypeAlias = "str | FirstName"
ExaminerLastNameInput: TypeAlias = "str | LastName"


class _ExaminerNameSource(Protocol):
    name: str


class Examiner(Person):
    center: models.ForeignKey["Center | None"] = models.ForeignKey(
        "Center", on_delete=models.CASCADE, blank=True, null=True
    )
    hash: models.CharField[str, Any] = models.CharField(max_length=255, unique=True)
    identity_salt_fingerprint: models.CharField[str, str] = models.CharField(
        max_length=64, blank=True, default="", editable=False
    )
    identity_fingerprint: models.CharField[str, str] = models.CharField(
        max_length=64, blank=True, default="", editable=False
    )

    if TYPE_CHECKING:
        center_id: int | None
        portal_user_info: PortalUserInfo

    def __str__(self) -> str:
        return self.first_name + " " + self.last_name

    @classmethod
    @identity_rotation_transaction
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
        )
        rotate_examiner_match(
            first_name=first_name,
            last_name=last_name,
            center_name=center.name,
            center_id=int(center.pk),
            active_hash=real_hash,
        )

        if substitute_names:
            name_tuple = create_mock_examiner_name()

        else:
            name_tuple = (first_name, last_name)
        defaults = dict(
            first_name=name_tuple[0],
            last_name=name_tuple[1],
            center=center,
            **examiner_identity_fields(first_name, last_name, int(center.pk)),
        )
        examiner, created = cls.objects.get_or_create(hash=real_hash, defaults=defaults)
        return examiner, created

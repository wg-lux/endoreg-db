from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, cast

from django.db import models

from endoreg_db.services.secret_rotation.identity import (
    examiner_identity_fields,
    identity_rotation_transaction,
    rotate_examiner_match,
)
from endoreg_db.utils import create_mock_examiner_name
from endoreg_db.utils.hashs import get_examiner_hash

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
        return f"{self.first_name} {self.last_name}"

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

        raw_first = (
            cast(_ExaminerNameSource, first_name).name
            if isinstance(first_name, FirstName)
            else str(first_name)
        )
        raw_last = (
            cast(_ExaminerNameSource, last_name).name
            if isinstance(last_name, LastName)
            else str(last_name)
        )

        center_key = center.center_key or center.name

        # Calculate canonical hash using center_key directly (no extra DB query)
        real_hash = get_examiner_hash(
            first_name=raw_first,
            last_name=raw_last,
            center_key=center_key,
        )

        # Trigger rotation check for historical hash upgrades
        rotate_examiner_match(
            first_name=raw_first,
            last_name=raw_last,
            center_name=center.name,
            center_id=int(center.pk),
            active_hash=real_hash,
        )

        if substitute_names:
            name_tuple = create_mock_examiner_name()
        else:
            name_tuple = (raw_first, raw_last)

        # Bind identity fields to center_key so hash and identity_fingerprint match
        defaults = dict(
            first_name=name_tuple[0],
            last_name=name_tuple[1],
            center=center,
            **examiner_identity_fields(raw_first, raw_last, center_key),
        )
        examiner, created = cls.objects.get_or_create(hash=real_hash, defaults=defaults)
        return examiner, created

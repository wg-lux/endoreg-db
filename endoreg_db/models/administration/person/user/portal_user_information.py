from __future__ import annotations

from types import NoneType
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast

from django.db import models

# models.py in your main app

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from ..examiner.examiner import Examiner
    from ..profession import Profession

NoPortalUserInfoValue: TypeAlias = NoneType
PortalUserInfoFlag: TypeAlias = bool | NoPortalUserInfoValue


class _PortalUserSource(Protocol):
    username: str


class PortalUserInfo(models.Model):
    user: models.OneToOneField["User", "User"] = models.OneToOneField(
        "auth.User", on_delete=models.CASCADE
    )
    profession: models.ForeignKey[
        Profession | NoPortalUserInfoValue,
        Profession | NoPortalUserInfoValue,
    ] = models.ForeignKey(
        "endoreg_db.Profession",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="portal_user_infos",
    )
    works_in_endoscopy: models.BooleanField[
        PortalUserInfoFlag,
        PortalUserInfoFlag,
    ] = models.BooleanField(blank=True, null=True)
    # Add other fields as needed

    examiner: models.OneToOneField[
        Examiner | NoPortalUserInfoValue,
        Examiner | NoPortalUserInfoValue,
    ] = models.OneToOneField(
        "endoreg_db.Examiner",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="portal_user_info",
    )

    if TYPE_CHECKING:
        pass

    def __str__(self) -> str:
        user = cast(_PortalUserSource, self.user)
        return user.username

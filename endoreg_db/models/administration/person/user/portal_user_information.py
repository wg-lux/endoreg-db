from __future__ import annotations

from typing import Protocol, TypeAlias, cast, Any

from django.db import models

# models.py in your main app

NoPortalUserInfoValue: TypeAlias = None
PortalUserInfoFlag: TypeAlias = bool | NoPortalUserInfoValue


class _PortalUserSource(Protocol):
    username: str


class PortalUserInfo(models.Model):
    user: models.OneToOneField[Any, Any] = models.OneToOneField(
        "auth.User", on_delete=models.CASCADE
    )
    profession: models.ForeignKey[Any, Any] = models.ForeignKey(
        "endoreg_db.Profession",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="portal_user_infos",
    )
    works_in_endoscopy: models.BooleanField[Any, Any] = models.BooleanField(
        blank=True, null=True
    )
    # Add other fields as needed

    examiner: models.OneToOneField[Any, Any] = models.OneToOneField(
        "endoreg_db.Examiner",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="portal_user_info",
    )
    centers: models.ManyToManyField[Any, Any] = models.ManyToManyField(
        "endoreg_db.Center",
        blank=True,
        related_name="authorized_portal_user_infos",
    )

    def __str__(self) -> str:
        user = cast(_PortalUserSource, self.user)
        return user.username

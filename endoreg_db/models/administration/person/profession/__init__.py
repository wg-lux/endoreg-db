from __future__ import annotations

from types import NoneType
from typing import TYPE_CHECKING, TypeAlias

from django.db import models

if TYPE_CHECKING:
    from ..user.portal_user_information import PortalUserInfo

NoProfessionValue: TypeAlias = NoneType
ProfessionDescription: TypeAlias = str | NoProfessionValue


class ProfessionManager(models.Manager["Profession"]):
    def get_by_natural_key(self, name: str) -> "Profession":
        return self.get(name=name)


class Profession(models.Model):
    objects = ProfessionManager()
    name: models.CharField[str, str] = models.CharField(max_length=100)
    description: models.TextField[
        ProfessionDescription,
        ProfessionDescription,
    ] = models.TextField(blank=True, null=True)

    if TYPE_CHECKING:

        @property
        def portal_user_infos(self) -> models.QuerySet["PortalUserInfo"]: ...

    def __str__(self) -> str:
        """
        Return the profession's name as its string representation.
        """
        return str(self.name)

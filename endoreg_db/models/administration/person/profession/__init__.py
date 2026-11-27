from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager

if TYPE_CHECKING:
    from ..user.portal_user_information import PortalUserInfo


class Profession(models.Model):
    objects = NameNaturalKeyManager["Profession"]()
    name: models.CharField[Any, Any] = models.CharField(max_length=100)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)

    if TYPE_CHECKING:

        @property
        def portal_user_infos(self) -> models.QuerySet["PortalUserInfo"]: ...

    def __str__(self) -> str:
        """
        Return the profession's name as its string representation.
        """
        return str(self.name)

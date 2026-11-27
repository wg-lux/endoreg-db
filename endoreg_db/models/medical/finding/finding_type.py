from __future__ import annotations
from typing import TYPE_CHECKING, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager


class FindingType(models.Model):
    name: models.CharField[Any, Any] = models.CharField(max_length=100, unique=True)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)

    objects: models.Manager["FindingType"] = NameNaturalKeyManager["FindingType"]()  # pyright: ignore[reportIncompatibleVariableOverride]

    if TYPE_CHECKING:
        from endoreg_db.models import FindingClassification

        @property
        def finding_classifications(
            self,
        ) -> "models.Manager[FindingClassification]": ...

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)

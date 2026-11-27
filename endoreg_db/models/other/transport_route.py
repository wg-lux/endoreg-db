from __future__ import annotations
from typing import TYPE_CHECKING, ClassVar, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager

if TYPE_CHECKING:
    from endoreg_db.models import (
        Product,
    )


class TransportRoute(models.Model):
    objects: ClassVar[NameNaturalKeyManager["TransportRoute"]] = (  # pyright: ignore[reportIncompatibleVariableOverride]
        NameNaturalKeyManager["TransportRoute"]()
    )

    distance: models.FloatField[Any, Any] = models.FloatField()
    name: models.CharField[Any, Any] = models.CharField(max_length=255)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    emission_factor: models.ForeignKey[Any] = models.ForeignKey(
        "EmissionFactor", on_delete=models.SET_NULL, null=True
    )
    unit: models.ForeignKey[Any] = models.ForeignKey(
        "Unit", on_delete=models.SET_NULL, null=True
    )

    if TYPE_CHECKING:

        @property
        def products(self) -> models.QuerySet["Product"]: ...

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    def __str__(self) -> str:
        result = f"{self.name} ({self.distance} {self.unit}) - {self.emission_factor}"
        return result

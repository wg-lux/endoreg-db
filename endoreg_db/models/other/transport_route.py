from __future__ import annotations
from typing import TYPE_CHECKING, ClassVar, Any

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import (
        Product,
    )


class TransportRouteManager(models.Manager["TransportRoute"]):
    def get_by_natural_key(self, name: str) -> "TransportRoute":
        return self.get(name=name)


class TransportRoute(models.Model):
    objects: ClassVar[TransportRouteManager] = TransportRouteManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    distance: models.FloatField[Any, Any] = models.FloatField()
    name: models.CharField[Any, Any] = models.CharField(max_length=255)
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

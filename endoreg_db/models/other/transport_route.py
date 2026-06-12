from typing import TYPE_CHECKING, ClassVar

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import (
        EmissionFactor,
        Product,
        Unit,
    )


class TransportRouteManager(models.Manager["TransportRoute"]):
    def get_by_natural_key(self, name: str) -> "TransportRoute":
        return self.get(name=name)


class TransportRoute(models.Model):
    objects: ClassVar[TransportRouteManager] = TransportRouteManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    distance: models.FloatField[float, float] = models.FloatField()
    name: models.CharField[str, str] = models.CharField(max_length=255)
    emission_factor = models.ForeignKey(
        "EmissionFactor", on_delete=models.SET_NULL, null=True
    )
    unit = models.ForeignKey("Unit", on_delete=models.SET_NULL, null=True)

    if TYPE_CHECKING:
        emission_factor: models.ForeignKey["EmissionFactor|None", "EmissionFactor|None"]
        unit: models.ForeignKey["Unit|None", "Unit|None"]

        @property
        def products(self) -> models.QuerySet["Product"]: ...

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    def __str__(self) -> str:
        result = f"{self.name} ({self.distance} {self.unit}) - {self.emission_factor}"
        return result

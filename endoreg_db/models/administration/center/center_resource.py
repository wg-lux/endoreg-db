from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias, Any

from django.db import models

if TYPE_CHECKING:
    from ...other.emission import EmissionFactor

NoCenterResourceValue: TypeAlias = None
CenterResourceName: TypeAlias = str | NoCenterResourceValue


class CenterResource(models.Model):
    name: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        null=True,
    )
    center: models.ForeignKey[Any] = models.ForeignKey(
        "Center",
        on_delete=models.CASCADE,
        related_name="center_resources",
    )
    quantity: models.FloatField[Any, Any] = models.FloatField()
    resource: models.ForeignKey[Any] = models.ForeignKey(
        "Resource",
        on_delete=models.CASCADE,
    )
    transport_emission_factor: models.ForeignKey[
        EmissionFactor | NoCenterResourceValue | None
    ] = models.ForeignKey(
        "EmissionFactor",
        on_delete=models.SET_NULL,
        null=True,
        related_name="center_resource_transport_emission_factor",
    )
    use_emission_factor: models.ForeignKey[
        EmissionFactor | NoCenterResourceValue | None
    ] = models.ForeignKey(
        "EmissionFactor",
        on_delete=models.SET_NULL,
        null=True,
        related_name="center_resource_use_emission_factor",
    )
    year: models.IntegerField[Any, Any] = models.IntegerField()
    unit: models.ForeignKey[Any] = models.ForeignKey(
        "Unit", on_delete=models.SET_NULL, null=True
    )

    if TYPE_CHECKING:
        pass

    def __str__(self) -> str:
        return f"CenterResource {self.pk} - {self.name if self.name else 'No Name'}"

    def display_str(self) -> str:
        """Return a verbose description of the center resource."""
        result_string = f"{self.name}:\n"

        result_string += "\tCenter\t-\t" + str(self.center) + "\n"
        result_string += "\tResource\t-\t" + str(self.resource) + "\n"
        result_string += "\tQuantity\t-\t" + str(self.quantity) + "\n"
        result_string += "\tYear\t-\t" + str(self.year) + "\n"
        result_string += "\tUnit\t-\t" + str(self.unit) + "\n"

        if self.transport_emission_factor is not None:
            result_string += (
                "\tTransport Emission Factor\t-\t"
                + str(self.transport_emission_factor)
                + "\n"
            )

        result_string += (
            "\tUse Emission Factor\t-\t" + str(self.use_emission_factor) + "\n"
        )

        result_string += "\n"

        return result_string

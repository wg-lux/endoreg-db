from __future__ import annotations

from typing import TypeAlias, Any

from django.db import models

NoCenterWasteValue: TypeAlias = None


class CenterWaste(models.Model):
    center: models.ForeignKey[Any] = models.ForeignKey(
        "Center",
        on_delete=models.CASCADE,
        related_name="center_wastes",
    )
    year: models.IntegerField[Any, Any] = models.IntegerField()
    waste: models.ForeignKey[Any] = models.ForeignKey(
        "Waste",
        on_delete=models.CASCADE,
    )
    quantity: models.FloatField[Any, Any] = models.FloatField()
    unit: models.ForeignKey[Any] = models.ForeignKey(
        "Unit", on_delete=models.SET_NULL, null=True
    )
    emission_factor: models.ForeignKey[Any] = models.ForeignKey(
        "EmissionFactor", on_delete=models.SET_NULL, null=True
    )

    def __str__(self) -> str:
        """
        Returns a formatted string summarizing the waste record, including quantity, unit,
        waste type, emission factor, center, and year.
        """
        result = f"{self.quantity} {self.unit}"
        result += f" -\t{self.waste}, EmissionFactor: {self.emission_factor}\t\t- {self.center} - {self.year}"

        return result

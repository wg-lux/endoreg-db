from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from ...other.emission import EmissionFactor
    from ...other.unit import Unit
    from ...other.waste import Waste
    from .center import Center


class CenterWaste(models.Model):
    if TYPE_CHECKING:
        center: models.ForeignKey[Center, Center]
        waste: models.ForeignKey[Waste, Waste]
        unit: models.ForeignKey[Unit | None, Unit | None]
        emission_factor: models.ForeignKey[EmissionFactor | None, EmissionFactor | None]

    center = models.ForeignKey(
        "Center",
        on_delete=models.CASCADE,
        related_name="center_wastes",
    )
    year = models.IntegerField()
    waste = models.ForeignKey("Waste", on_delete=models.CASCADE)
    quantity = models.FloatField()
    unit = models.ForeignKey("Unit", on_delete=models.SET_NULL, null=True)
    emission_factor = models.ForeignKey(
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

from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .center import Center
    from ...other.waste import Waste
    from ...other.unit import Unit
    from ...other.emission import EmissionFactor
    
class CenterWaste(models.Model):
    center: models.ForeignKey["Center"] = models.ForeignKey(
        "Center",
        on_delete=models.CASCADE,
        related_name="center_wastes",
    )
    year = models.IntegerField()
    waste: models.ForeignKey["Waste"] = models.ForeignKey("Waste", on_delete=models.CASCADE)
    quantity = models.FloatField()
    unit: models.ForeignKey["Unit"] = models.ForeignKey("Unit", on_delete=models.SET_NULL, null=True)
    emission_factor: models.ForeignKey["EmissionFactor"] = models.ForeignKey("EmissionFactor", on_delete=models.SET_NULL, null=True)
    
    def __str__(self) -> str:
        """
        Returns a formatted string summarizing the waste record, including quantity, unit,
        waste type, emission factor, center, and year.
        """
        result = f"{self.quantity} {self.unit}"
        result += f" -\t{self.waste}, EmissionFactor: {self.emission_factor}\t\t- {self.center} - {self.year}"

        return result

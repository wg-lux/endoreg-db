from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .center import Center
    from ...other.resource import Resource
    from ...other.unit import Unit
    from ...other.emission import EmissionFactor
    
class CenterResource(models.Model):
    name = models.CharField(max_length=255, null=True)
    center = models.ForeignKey(
        "Center",
        on_delete=models.CASCADE,
        related_name="center_resources",
    )
    quantity = models.FloatField()
    resource = models.ForeignKey("Resource", on_delete=models.CASCADE)
    transport_emission_factor = models.ForeignKey("EmissionFactor", on_delete=models.SET_NULL, null=True,
                                                  related_name="center_resource_transport_emission_factor")
    use_emission_factor = models.ForeignKey("EmissionFactor", on_delete=models.SET_NULL, null=True,
                                            related_name="center_resource_use_emission_factor")
    year = models.IntegerField()
    unit = models.ForeignKey("Unit", on_delete=models.SET_NULL, null=True)
    
    if TYPE_CHECKING:
        center: "Center"
        resource: "Resource"
        transport_emission_factor: "EmissionFactor"
        use_emission_factor: "EmissionFactor"
        unit: "Unit"

    def __str__(self):
        result_string = ""

        if self.name is not None:
            result_string += self.name + ":\n"

        result_string += "\tCenter\t-\t" + str(self.center) + "\n"
        result_string += "\tResource\t-\t" + str(self.resource) + "\n"
        result_string += "\tQuantity\t-\t" + str(self.quantity) + "\n"
        result_string += "\tYear\t-\t" + str(self.year) + "\n"
        result_string += "\tUnit\t-\t" + str(self.unit) + "\n"
        result_string += "\tTransport Emission Factor\t-\t" + str(self.transport_emission_factor) + "\n"
        result_string += "\tUse Emission Factor\t-\t" + str(self.use_emission_factor) + "\n"

        result_string += "\n"

        return result_string
from os import name
from django.db import models

class CenterResource(models.Model):
    name = models.CharField(max_length=255, null=True)
    center = models.ForeignKey("Center", on_delete=models.CASCADE)
    name_de = models.CharField(max_length=255, null=True)
    name_en = models.CharField(max_length=255, null=True)
    quantity = models.FloatField()
    resource = models.ForeignKey("Resource", on_delete=models.CASCADE)
    transport_emission_factor = models.ForeignKey("EmissionFactor", on_delete=models.SET_NULL, null=True,
                                                  related_name="center_resource_transport_emission_factor")
    use_emission_factor = models.ForeignKey("EmissionFactor", on_delete=models.SET_NULL, null=True,
                                            related_name="center_resource_use_emission_factor")
    year = models.IntegerField()
    unit = models.ForeignKey("Unit", on_delete=models.SET_NULL, null=True)
    
    def __str__(self):
        return self.center.name + " - " + self.resource.name + " (" + str(self.year) + ")"
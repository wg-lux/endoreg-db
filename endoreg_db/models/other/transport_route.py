from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import (
        Product,
        EmissionFactor,
        Unit,
    )

class TransportRouteManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class TransportRoute(models.Model):
    objects = TransportRouteManager()

    distance = models.FloatField()
    name = models.CharField(max_length=255)
    emission_factor = models.ForeignKey("EmissionFactor", on_delete=models.SET_NULL, null=True)
    unit = models.ForeignKey("Unit", on_delete=models.SET_NULL, null=True)

    if TYPE_CHECKING:
        emission_factor: "EmissionFactor"
        unit: "Unit"
        products: models.QuerySet["Product"]

    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        result = f"{self.name} ({self.distance} {self.unit}) - {self.emission_factor}"
        return result
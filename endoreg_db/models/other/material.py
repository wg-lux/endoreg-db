from django.db import models
from typing import TYPE_CHECKING

from endoreg_db.models.administration.product import product_material

if TYPE_CHECKING:
    from .emission import EmissionFactor
    from ..administration.product.product_material import ProductMaterial

class MaterialManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class Material(models.Model):
    objects = MaterialManager()

    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, null=True)
    name_en = models.CharField(max_length=255, null=True)
    emission_factor = models.ForeignKey("EmissionFactor", on_delete=models.SET_NULL, null=True)

    if TYPE_CHECKING:
        emission_factor: "EmissionFactor"
        product_materials: models.QuerySet["ProductMaterial"]

    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        result = f"{self.name} - EmissionFactor: {self.emission_factor}"
        return result

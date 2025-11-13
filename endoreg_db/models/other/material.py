from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from ..administration.product.product_material import ProductMaterial
    from .emission import EmissionFactor


class MaterialManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class Material(models.Model):
    objects = MaterialManager()

    name = models.CharField(max_length=255)
    emission_factor = models.ForeignKey("EmissionFactor", on_delete=models.SET_NULL, null=True)

    if TYPE_CHECKING:
        emission_factor: models.ForeignKey["EmissionFactor|None"]

        @property
        def material_product_materials(self) -> models.QuerySet["ProductMaterial"]: ...

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        result = f"{self.name} - EmissionFactor: {self.emission_factor}"
        return result

from django.db import models
from typing import TYPE_CHECKING

from endoreg_db.models.other.unit import Unit

if TYPE_CHECKING:
    from ...other.emission import EmissionFactor
    from ...other.unit import Unit
    from .product import Product
    from ...other.material import Material

class ProductMaterial(models.Model):
    component = models.CharField(max_length=255)
    material = models.ForeignKey(
        "Material",
        on_delete=models.CASCADE,
        related_name="material_product_materials", # Changed related_name
    )
    product = models.ForeignKey(
        "Product",
        on_delete=models.CASCADE,
        related_name="product_product_materials" # Changed related_name
    )
    unit = models.ForeignKey(
        "Unit",
        on_delete=models.CASCADE,
        related_name="unit_product_materials", # Changed related_name
    )
    quantity = models.FloatField()

    if TYPE_CHECKING:
        product: "Product"
        material: "Material"
        unit: "Unit"
        emission_factor: "EmissionFactor"

    def get_emission(self) -> tuple[float, Unit]:
        emission_factor = self.material.emission_factor
        if emission_factor is None:
            raise Exception("No emission factor for material " + self.material.name + " found.")
        
        # make sure product_material.unit is the same as emission_factor.unit
        if self.unit != emission_factor.unit:
            raise Exception("Unit mismatch: " + self.unit.name + " != " + emission_factor.unit.name)
        
        emmision_value = emission_factor.value * self.quantity
        emission_unit = emission_factor.unit
        return emmision_value, emission_unit
    
    def __str__(self) -> str:
        return f"{self.product.name} - {self.material.name} - {self.quantity} {self.unit.name}"




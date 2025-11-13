from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from ..administration.product.product_material import ProductMaterial


class UnitManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class Unit(models.Model):
    objects = UnitManager()

    name = models.CharField(max_length=100)  # e.g. "Centimeter"
    description = models.CharField(max_length=100, blank=True, null=True)  # e.g. "centimeters", "milimeters", "inches"
    abbreviation = models.CharField(max_length=25, blank=True, null=True)  # e.g. "cm", "mm", "in"

    if TYPE_CHECKING:
        unit_product_materials: models.QuerySet["ProductMaterial"]

    def __str__(self):
        if self.abbreviation:
            return str(self.abbreviation)
        return str(self.name)

    def natural_key(self):
        """
        Return a tuple containing the unit's name for natural key serialization.

        Returns:
            tuple: A single-element tuple with the unit's name.
        """
        return (self.name,)

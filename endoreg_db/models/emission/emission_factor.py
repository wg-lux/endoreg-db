from django.db import models
from typing import List
class EmissionFactorManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

# get debug from settings
# from django.conf import settings
# DEBUG = settings.DEBUG

class EmissionFactor(models.Model):
    objects = EmissionFactorManager()

    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, null=True)
    name_en = models.CharField(max_length=255, null=True)
    unit = models.ForeignKey("Unit", on_delete=models.SET_NULL, null=True)
    value = models.FloatField()
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self, verbose=False):
        result = f"{self.name}:\t{self.value} per {self.unit}"
        if verbose:
            result += f"\n\tSources:"
            for source in self.sources():
                result += f"\n\t\t{source}"

        return result
    
    def get_reference_products(self):
        
        from endoreg_db.models import (
            ReferenceProduct
        )

        reference_products = []
        reference_products += [
            _ for _ in ReferenceProduct.objects.filter(emission_factor_total=self)
        ]
        reference_products += [
            _ for _ in ReferenceProduct.objects.filter(emission_factor_package=self)
        ]
        reference_products += [
            _ for _ in ReferenceProduct.objects.filter(emission_factor_product=self)
        ]

        return reference_products


    def sources(self):
        sources = []
        sources.append(self.get_reference_products())

        return sources

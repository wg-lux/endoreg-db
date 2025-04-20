from django.db import models
from typing import List
class EmissionFactorManager(models.Manager):
    """
    Manager for EmissionFactor with custom query methods.
    """
    def get_by_natural_key(self, name: str) -> "EmissionFactor":
        return self.get(name=name)

# get debug from settings
# from django.conf import settings
# DEBUG = settings.DEBUG

class EmissionFactor(models.Model):
    """
    Represents an emission factor with associated unit and value.

    Attributes:
        name (str): The name of the emission factor.
        name_de (str): The German name of the emission factor.
        name_en (str): The English name of the emission factor.
        unit (ForeignKey): The unit associated with the emission factor.
        value (float): The value of the emission factor.
    """
    objects = EmissionFactorManager()

    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, null=True)
    name_en = models.CharField(max_length=255, null=True)
    unit = models.ForeignKey("Unit", on_delete=models.SET_NULL, null=True)
    value = models.FloatField()
    
    def natural_key(self) -> tuple:
        """
        Returns the natural key for the emission factor.

        Returns:
            tuple: The natural key consisting of the name.
        """
        return (self.name,)

    def __str__(self, verbose: bool = False) -> str:
        """
        String representation of the emission factor.

        Args:
            verbose (bool): If True, includes additional details.

        Returns:
            str: The string representation of the emission factor.
        """
        result = f"{self.name}:	{self.value} per {self.unit}"
        if verbose:
            result += "\n\tSources:"
            for source in self.sources():
                result += f"\n\t\t{source}"

        return result
    
    def get_reference_products(self) -> List["ReferenceProduct"]:
        """
        Retrieves all reference products associated with the emission factor.

        Returns:
            list: A list of ReferenceProduct instances associated with this emission factor.
        """
        from endoreg_db.models import ReferenceProduct

        reference_products = []
        reference_products += ReferenceProduct.objects.filter(emission_factor_total=self)
        reference_products += ReferenceProduct.objects.filter(emission_factor_package=self)
        reference_products += ReferenceProduct.objects.filter(emission_factor_product=self)

        return reference_products

    def sources(self) -> List:
        """
        Retrieves all sources related to the emission factor.

        Returns:
            list: A list of sources related to the emission factor.
        """
        sources = []
        sources.extend(self.get_reference_products())
        return sources

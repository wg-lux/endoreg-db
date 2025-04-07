from django.db import models
from typing import TYPE_CHECKING


class RiskTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a RiskType instance by its natural key.
        
        Args:
            name (str): The natural key value corresponding to the risk type's name.
        
        Returns:
            RiskType: The risk type instance matching the provided name.
        """
        return self.get(name=name)


class RiskType(models.Model):
    """
    A class representing a risk type.

    Attributes:
        name (str): The name of the risk type.
        name_de (str): The German name of the risk type.
        name_en (str): The English name of the risk type.
        description (str): A description of the risk type.
    """

    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    objects = RiskTypeManager()

    if TYPE_CHECKING:
        from endoreg_db.models.risk.risk import Risk

        risks: models.QuerySet[Risk]

    def natural_key(self):
        """
        Returns the natural key for this risk type instance.
        
        The natural key is defined as a single-element tuple containing the unique name,
        which facilitates natural key serialization in Django.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of this risk type.
        
        This method returns the instance's name attribute as a string.
        """
        return str(self.name)

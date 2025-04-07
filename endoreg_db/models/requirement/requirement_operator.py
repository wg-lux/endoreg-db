from django.db import models
from typing import TYPE_CHECKING


class RequirementOperatorManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a requirement operator using its natural key.
        
        Args:
            name: The unique name identifying the requirement operator.
        
        Returns:
            The RequirementOperator instance matching the provided name.
        """
        return self.get(name=name)


class RequirementOperator(models.Model):
    """
    A class representing a requirement operator.

    Attributes:
        name (str): The name of the requirement operator.
        name_de (str): The German name of the requirement operator.
        name_en (str): The English name of the requirement operator.
        description (str): A description of the requirement operator.
    """

    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    objects = RequirementOperatorManager()

    if TYPE_CHECKING:
        from endoreg_db.models.requirement.requirement import Requirement

        requirements: models.QuerySet[Requirement]

    def natural_key(self):
        """
        Returns the natural key for this requirement operator.
        
        The natural key is represented as a tuple containing only the operator's name, which is used for natural key serialization.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the requirement operator.
        
        Returns:
            str: The operator's name.
        """
        return str(self.name)

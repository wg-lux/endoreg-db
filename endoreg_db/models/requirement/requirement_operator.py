from django.db import models
from typing import TYPE_CHECKING


class RequirementOperatorManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieve a RequirementOperator instance by its natural key.
        
        Args:
            name (str): The unique name representing the natural key.
        
        Returns:
            RequirementOperator: The model instance matching the provided name.
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
        Return the natural key for the requirement operator.
        
        This method returns a tuple containing the operator's name, which serves as its
        natural key for serialization and unique identification within the system.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the requirement operator.
        
        Returns:
            str: The name attribute of the operator.
        """
        return str(self.name)

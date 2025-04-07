from django.db import models
from typing import TYPE_CHECKING


class RequirementOperatorManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a requirement operator instance using its natural key.
        
        Args:
            name: The unique name identifying the requirement operator.
        
        Returns:
            The RequirementOperator instance with the matching name.
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
        Return a tuple with the instance's name as its natural key.
        
        This method returns a tuple containing the instance's 'name' attribute, which uniquely
        identifies the object for natural key serialization and lookup.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the operator's name as its string representation.
        """
        return str(self.name)

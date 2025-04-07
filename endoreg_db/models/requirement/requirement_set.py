from django.db import models
from typing import TYPE_CHECKING


class RequirementSetTypeManager(models.Manager):
    """
    Manager for RequirementSetType with custom query methods.
    """

    def get_by_natural_key(self, name: str) -> "RequirementSetType":
        """
        Retrieves a RequirementSetType instance by its natural key.
        
        Args:
            name: The unique string corresponding to the RequirementSetType's name.
        
        Returns:
            The matching RequirementSetType instance.
        
        Raises:
            RequirementSetType.DoesNotExist: If no instance with the specified name exists.
        """
        return self.get(name=name)


class RequirementSetType(models.Model):
    """
    A class representing a type of requirement set.

    Attributes:
        name (str): The name of the requirement set type.
        name_de (str): The German name of the requirement set type.
        name_en (str): The English name of the requirement set type.
    """

    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    objects = RequirementSetTypeManager()

    if TYPE_CHECKING:
        from endoreg_db.models.requirement import RequirementType

        requirement_types: models.QuerySet[RequirementType]

    def natural_key(self):
        """
        Return a tuple containing the instance's natural key.
        
        The natural key is defined as a tuple with the single element, the instance's name.
        """
        return (self.name,)


class RequirementSetManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a model instance using its natural key.
        
        Args:
            name (str): The natural key used to identify the instance.
        
        Returns:
            The model instance with the matching name.
        """
        return self.get(name=name)


class RequirementSet(models.Model):
    """
    A class representing a set of requirements.

    Attributes:
        name (str): The name of the requirement set.
        name_de (str): The German name of the requirement set.
        name_en (str): The English name of the requirement set.
        description (str): A description of the requirement set.
    """

    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    requirements = models.ManyToManyField(
        "Requirement",
        blank=True,
        related_name="requirement_sets",
    )
    links_to_sets = models.ManyToManyField(
        "RequirementSet",
        blank=True,
        related_name="links_from_sets",
    )
    requirement_set_type = models.ForeignKey(
        "RequirementSetType",
        on_delete=models.CASCADE,
        related_name="requirement_sets",
        blank=True,
        null=True,
    )
    information_sources = models.ManyToManyField(
        "InformationSource",
        related_name="requirement_sets",
        blank=True,
    )
    objects = RequirementSetManager()

    if TYPE_CHECKING:
        from endoreg_db.models import Requirement, InformationSource

        requirements: models.QuerySet[Requirement]
        information_source: InformationSource
        requirement_set_type: RequirementSetType
        linked_sets: models.QuerySet["RequirementSet"]

    def natural_key(self):
        """
        Return the natural key for the instance.
        
        The natural key is defined as a tuple containing the instance's name, which is used for natural key lookups.
        """
        return (self.name,)

    def __str__(self):
        """Return the string representation of the instance.
        
        The method returns the instance's `name` attribute converted to a string.
        """
        return str(self.name)

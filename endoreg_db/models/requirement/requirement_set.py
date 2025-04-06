from django.db import models
from typing import TYPE_CHECKING


class RequirementSetTypeManager(models.Manager):
    """
    Manager for RequirementSetType with custom query methods.
    """

    def get_by_natural_key(self, name: str) -> "RequirementSetType":
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
        return (self.name,)


class RequirementSetManager(models.Manager):
    def get_by_natural_key(self, name):
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
        return (self.name,)

    def __str__(self):
        return str(self.name)

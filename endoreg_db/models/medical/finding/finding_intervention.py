from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import InformationSource


class FindingInterventionManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class FindingIntervention(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    intervention_types: "models.ManyToManyField['FindingInterventionType', 'FindingInterventionType']" = models.ManyToManyField(
        "FindingInterventionType", blank=True, related_name="interventions"
    )
    information_sources: "models.ManyToManyField[InformationSource, InformationSource]" = models.ManyToManyField(
        "InformationSource",
        related_name="finding_interventions",
        blank=True,
    )
    objects = FindingInterventionManager()

    if TYPE_CHECKING:
        pass

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return str(self.name)


class FindingInterventionTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class FindingInterventionType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    objects = FindingInterventionTypeManager()

    if TYPE_CHECKING:

        @property
        def interventions(
            self,
        ) -> "models.Manager[FindingIntervention]": ...

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return str(self.name)

from typing import TYPE_CHECKING, cast

from django.db import models


class FindingInterventionManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class FindingIntervention(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    intervention_types = models.ManyToManyField(
        "FindingInterventionType", blank=True, related_name="interventions"
    )
    information_sources = models.ManyToManyField(
        "InformationSource",
        related_name="finding_interventions",
        blank=True,
    )
    objects = FindingInterventionManager()

    if TYPE_CHECKING:
        from endoreg_db.models import (
            Contraindication,
            FindingInterventionType,
            InformationSource,
            LabValue,
        )

        intervention_types = cast(
            models.manager.RelatedManager["FindingInterventionType"], intervention_types
        )
        information_sources = cast(
            models.manager.RelatedManager["InformationSource"], information_sources
        )

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
        ) -> "models.manager.RelatedManager[FindingIntervention]": ...

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return str(self.name)

from __future__ import annotations
from typing import TYPE_CHECKING, Any

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import InformationSource


class FindingInterventionManager(models.Manager["FindingIntervention"]):
    def get_by_natural_key(self, name: str) -> "FindingIntervention":
        return self.get(name=name)


class FindingIntervention(models.Model):
    name: models.CharField[Any, Any] = models.CharField(max_length=100, unique=True)
    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)
    intervention_types: models.ManyToManyField[
        "FindingInterventionType",
        "FindingInterventionType",
    ] = models.ManyToManyField(
        "FindingInterventionType", blank=True, related_name="interventions"
    )
    information_sources: models.ManyToManyField[
        "InformationSource",
        "InformationSource",
    ] = models.ManyToManyField(
        "InformationSource",
        related_name="finding_interventions",
        blank=True,
    )
    objects = FindingInterventionManager()

    if TYPE_CHECKING:
        from endoreg_db.models import FindingInterventionType

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)


class FindingInterventionTypeManager(models.Manager["FindingInterventionType"]):
    def get_by_natural_key(self, name: str) -> "FindingInterventionType":
        return self.get(name=name)


class FindingInterventionType(models.Model):
    name: models.CharField[Any, Any] = models.CharField(max_length=100, unique=True)
    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)

    objects = FindingInterventionTypeManager()

    if TYPE_CHECKING:

        @property
        def interventions(
            self,
        ) -> "models.Manager[FindingIntervention]": ...

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)

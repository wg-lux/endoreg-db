from __future__ import annotations
from typing import TYPE_CHECKING, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager

if TYPE_CHECKING:
    from endoreg_db.models import InformationSource


class FindingIntervention(models.Model):
    name: models.CharField[Any, Any] = models.CharField(max_length=100, unique=True)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
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
    objects = NameNaturalKeyManager["FindingIntervention"]()

    if TYPE_CHECKING:
        from endoreg_db.models import FindingInterventionType

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)


class FindingInterventionType(models.Model):
    name: models.CharField[Any, Any] = models.CharField(max_length=100, unique=True)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)

    objects = NameNaturalKeyManager["FindingInterventionType"]()

    if TYPE_CHECKING:

        @property
        def interventions(
            self,
        ) -> "models.Manager[FindingIntervention]": ...

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)

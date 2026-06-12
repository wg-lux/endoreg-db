from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import (
        FindingClassification,
        FindingIntervention,
        FindingType,
        InformationSource,
    )


class FindingManager(models.Manager["Finding"]):
    def get_by_natural_key(self, name: str) -> "Finding":
        return self.get(name=name)


class Finding(models.Model):
    name: models.CharField[str, str] = models.CharField(max_length=100, unique=True)
    description: models.TextField[str, str] = models.TextField(blank=True, null=True)
    finding_types: "models.ManyToManyField[FindingType, FindingType]" = (
        models.ManyToManyField("FindingType", blank=True, related_name="findings")
    )
    finding_interventions: "models.ManyToManyField[FindingIntervention, FindingIntervention]" = models.ManyToManyField(
        "FindingIntervention", blank=True, related_name="findings"
    )
    caused_by_interventions: "models.ManyToManyField[FindingIntervention, FindingIntervention]" = models.ManyToManyField(
        "FindingIntervention", blank=True, related_name="causes_findings"
    )
    finding_classifications: "models.ManyToManyField[FindingClassification, FindingClassification]" = models.ManyToManyField(
        "FindingClassification", blank=True, related_name="findings"
    )
    information_sources: "models.ManyToManyField[InformationSource, InformationSource]" = models.ManyToManyField(
        "InformationSource", blank=True, related_name="findings"
    )
    objects = FindingManager()

    if TYPE_CHECKING:
        from endoreg_db.models import FindingClassification

    def natural_key(self) -> tuple[str]:
        """
        Return a tuple containing the unique natural key for this Finding instance.

        Returns:
            tuple: A single-element tuple with the Finding's name.
        """
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)

    def get_finding_types(self) -> models.QuerySet["FindingType"]:
        """
        Return all finding types associated with this finding.

        Returns:
            QuerySet: All related FindingType instances.
        """
        return self.finding_types.all()

    def get_classifications(
        self, classification_type: str = ""
    ) -> models.QuerySet["FindingClassification"]:
        """
        Retrieve all classifications associated with this finding, optionally filtered by classification type.

        Parameters:
                classification_type (str, optional): If provided, only classifications with a matching type name are returned.

        Returns:
                List[FindingClassification]: List of related classification objects, filtered by type if specified.
        """
        if classification_type:
            return self.finding_classifications.filter(
                classification_types__name=classification_type
            )
        return self.finding_classifications.all()

    def get_location_classifications(self) -> models.QuerySet["FindingClassification"]:
        """
        Retrieve all related FindingClassification objects with classification type 'location'.

        Returns:
            QuerySet: All FindingClassification instances linked to this finding where the classification type name is 'location' (case-insensitive).
        """
        return self.finding_classifications.filter(
            classification_types__name__iexact="location"
        )

    def get_morphology_classifications(
        self,
    ) -> models.QuerySet["FindingClassification"]:
        """
        Retrieve all related FindingClassification objects with classification type 'morphology'.

        Returns:
            QuerySet: A queryset of FindingClassification instances associated with this finding and classified as 'morphology'.
        """
        return self.finding_classifications.filter(
            classification_types__name__iexact="morphology"
        )

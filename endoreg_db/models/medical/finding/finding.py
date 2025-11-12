# Class to represent findings of examinations
from typing import TYPE_CHECKING, Optional, cast

from django.db import models


class FindingManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class Finding(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    examinations = models.ManyToManyField("Examination", blank=True, related_name="findings")
    finding_types = models.ManyToManyField("FindingType", blank=True, related_name="findings")

    finding_interventions = models.ManyToManyField("FindingIntervention", blank=True, related_name="findings")

    objects = FindingManager()

    if TYPE_CHECKING:
        from endoreg_db.models import (
            Examination,
            FindingClassification,
            FindingClassificationType,
            FindingIntervention,
            FindingType,
            PatientFindingClassification,
        )

        finding_types = cast(models.manager.RelatedManager["FindingType"], finding_types)
        examinations = cast(models.manager.RelatedManager["Examination"], examinations)
        finding_interventions = cast(models.manager.RelatedManager["FindingIntervention"], finding_interventions)

        @property
        def finding_classifications(self) -> "models.manager.RelatedManager[FindingClassification]": ...

        # finding_classifications: models.QuerySet["FindingClassification"]
        # examinations: models.QuerySet[Examination]
        # finding_types: models.QuerySet[FindingType]
        # finding_interventions: models.QuerySet[FindingIntervention]

    def natural_key(self):
        """
        Return a tuple containing the unique natural key for this Finding instance.

        Returns:
            tuple: A single-element tuple with the Finding's name.
        """
        return (self.name,)

    def __str__(self):
        return self.name

    def get_finding_types(self):
        """
        Return all finding types associated with this finding.

        Returns:
            QuerySet: All related FindingType instances.
        """
        return self.finding_types.all()

    def get_classifications(self, classification_type: Optional[str] = None) -> models.QuerySet["FindingClassification"]:
        """
        Retrieve all classifications associated with this finding, optionally filtered by classification type.

        Parameters:
                classification_type (str, optional): If provided, only classifications with a matching type name are returned.

        Returns:
                List[FindingClassification]: List of related classification objects, filtered by type if specified.
        """
        if classification_type:
            return self.finding_classifications.filter(classification_types__name=classification_type)
        return self.finding_classifications.all()

    def get_location_classifications(self):
        """
        Retrieve all related FindingClassification objects with classification type 'location'.

        Returns:
            QuerySet: All FindingClassification instances linked to this finding where the classification type name is 'location' (case-insensitive).
        """
        return self.finding_classifications.filter(classification_types__name__iexact="location")

    def get_morphology_classifications(self):
        """
        Retrieve all related FindingClassification objects with classification type 'morphology'.

        Returns:
            QuerySet: A queryset of FindingClassification instances associated with this finding and classified as 'morphology'.
        """
        return self.finding_classifications.filter(classification_types__name__iexact="morphology")

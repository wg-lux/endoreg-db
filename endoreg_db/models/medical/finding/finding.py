# Class to represent findings of examinations
from django.db import models
from typing import List

from typing import TYPE_CHECKING

class FindingManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class Finding(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    examinations = models.ManyToManyField('Examination', blank=True, related_name='findings')
    finding_types = models.ManyToManyField('FindingType', blank=True, related_name='findings')

    finding_interventions = models.ManyToManyField(
        'FindingIntervention',
        blank=True,
        related_name='findings'
    )

    objects = FindingManager()

    if TYPE_CHECKING:
        from endoreg_db.models import (
            Examination, FindingType, FindingIntervention, FindingClassificationType,
            FindingClassification,
            PatientFindingClassification
        )
    
        finding_classifications: models.QuerySet['FindingClassification']
        examinations: models.QuerySet[Examination]
        finding_types: models.QuerySet[FindingType]
        finding_interventions: models.QuerySet[FindingIntervention]
        
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
    
    def get_finding_types(self):
        return self.finding_types.all()
    
    def get_classifications(self, classification_type: str = None) -> models.QuerySet['FindingClassification']:
        """
        Returns all FindingClassification objects linked to this finding.
        If classification_type is provided, it filters by that type.
        
        :param classification_type: The type of classification to filter by (e.g., 'location', 'morphology').
        :return: A list of FindingClassification objects.
        """
        if classification_type:
            return self.finding_classifications.filter(classification_types__name=classification_type)
        return self.finding_classifications.all()
    
    def get_location_classifications(self):
        """
        Returns all FindingClassification objects of type 'location' linked to this finding.
        """
        return self.finding_classifications.filter(classification_types__name__iexact="location")

    def get_morphology_classifications(self):
        """
        Returns all FindingClassification objects of type 'morphology' linked to this finding.
        """
        return self.finding_classifications.filter(classification_types__name__iexact="morphology")

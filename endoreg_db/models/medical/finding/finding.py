# Class to represent findings of examinations
from django.db import models
from typing import List

from typing import TYPE_CHECKING

class FindingManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class Finding(models.Model):
    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    description_de = models.TextField(blank=True, null=True)
    description_en = models.TextField(blank=True, null=True)
    examinations = models.ManyToManyField('Examination', blank=True, related_name='findings')
    finding_types = models.ManyToManyField('FindingType', blank=True, related_name='findings')


    finding_interventions = models.ManyToManyField(
        'FindingIntervention',
        blank=True,
        related_name='findings'
    )

    causing_finding_interventions = models.ManyToManyField(
        'FindingIntervention',
        blank=True,
        related_name='causing_findings'
    )

    opt_causing_finding_interventions = models.ManyToManyField(
        'FindingIntervention',
        blank=True,
        related_name='opt_causing_findings'
    )

    required_morphology_classification_types = models.ManyToManyField(
        'FindingClassificationType',
        blank=True,
        related_name='required_by_findings'
    )

    optional_morphology_classification_types = models.ManyToManyField(
        'FindingClassificationType',
        blank=True,
        related_name='optional_for_findings'
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
        required_classifications: models.QuerySet['FindingClassification']
        available_classifications: models.QuerySet['FindingClassification']
        required_classification_types: models.QuerySet['FindingClassificationType'] # Logic not yet implemented, in future we want at least one classification of each type for this finding
        available_classification_types: models.QuerySet['FindingClassificationType']
        morphology_classifications: models.QuerySet['FindingClassification'] # To be deprecated
        location_classifications: models.QuerySet['FindingClassification'] # To be deprecated
        finding_types: models.QuerySet[FindingType]
        finding_interventions: models.QuerySet[FindingIntervention]
        
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
    
    def get_finding_types(self):
        return self.finding_types.all()
    
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
    
    def get_required_morphology_classification_types(self):
        from endoreg_db.models import FindingClassificationType
        finding_morphology_classification_types:List[FindingClassificationType] = [
            _ for _ in self.required_morphology_classification_types.all()
        ]
        return finding_morphology_classification_types


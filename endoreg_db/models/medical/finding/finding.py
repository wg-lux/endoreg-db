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
        'FindingMorphologyClassificationType',
        blank=True,
        related_name='required_by_findings'
    )

    optional_morphology_classification_types = models.ManyToManyField(
        'FindingMorphologyClassificationType',
        blank=True,
        related_name='optional_for_findings'
    )

    objects = FindingManager()

    if TYPE_CHECKING:
        from endoreg_db.models import (
            Examination, FindingType, FindingIntervention, FindingMorphologyClassificationType,
            FindingMorphologyClassification, FindingLocationClassification,
            FindingClassificationType, FindingClassification, FindingLocationClassificationChoice,
            PatientFindingClassification
        )
    
        finding_classifications: models.QuerySet['FindingClassification']
        examinations: models.QuerySet[Examination]
        morphology_classifications: models.QuerySet['FindingMorphologyClassification']
        location_classifications: models.QuerySet['FindingLocationClassification']
        finding_types: models.QuerySet[FindingType]
        finding_interventions: models.QuerySet[FindingIntervention]
        required_morphology_classification_types: models.QuerySet[FindingMorphologyClassificationType]
        optional_morphology_classification_types: models.QuerySet[FindingMorphologyClassificationType]
        
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
    
    def get_finding_types(self):
        return self.finding_types.all()
    
    def get_location_classifications(self):
        from endoreg_db.models import FindingLocationClassification
        # FindingLocationClassification is a class with a many-to-many relationship to Finding
        # related name is location_classifications

        location_classifications:FindingLocationClassification = self.location_classifications.all()
        
        return location_classifications 
    
    def get_morphology_classifications(self):
        from endoreg_db.models import FindingMorphologyClassification
        # Get morphology classifications through the classification types
        # Since Finding has relationships to FindingMorphologyClassificationType,
        # we need to get the classifications through these types
        
        # Get all required and optional morphology classification types for this finding
        required_types = self.required_morphology_classification_types.all()
        optional_types = self.optional_morphology_classification_types.all()
        
        # Combine both sets of types
        all_types = list(required_types) + list(optional_types)
        
        # Get all morphology classifications that belong to these types
        morphology_classifications = []
        for classification_type in all_types:
            # Get all classifications of this type
            classifications = FindingMorphologyClassification.objects.filter(
                classification_type=classification_type
            )
            morphology_classifications.extend(classifications)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_classifications = []
        for classification in morphology_classifications:
            if classification.id not in seen:
                seen.add(classification.id)
                unique_classifications.append(classification)
                
        return unique_classifications
    
    def get_required_morphology_classification_types(self):
        from endoreg_db.models import FindingMorphologyClassificationType
        finding_morphology_classification_types:List[FindingMorphologyClassificationType] = [
            _ for _ in self.required_morphology_classification_types.all()
        ]
        return finding_morphology_classification_types


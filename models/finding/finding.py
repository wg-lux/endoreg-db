# Class to represent findings of examinations
from django.db import models
from typing import List

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
    
    def get_required_morphology_classification_types(self):
        from endoreg_db.models import FindingMorphologyClassificationType
        finding_morphology_classification_types:List[FindingMorphologyClassificationType] = [
            _ for _ in self.required_morphology_classification_types.all()
        ]
        return finding_morphology_classification_types
    

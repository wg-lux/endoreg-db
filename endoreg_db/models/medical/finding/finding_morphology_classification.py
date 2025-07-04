# from random import choices
from django.db import models
from typing import TYPE_CHECKING, List

class FindingMorphologyClassificationTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class FindingMorphologyClassificationType(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True)
    name_en = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    
    objects = FindingMorphologyClassificationTypeManager()
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return str(self.name)
    
    @classmethod
    def get_required_classifications_for_finding(cls, finding):
        """
        Returns all required morphology classification types for a given finding.
        """
        required_classification_types = [
            _ for _ in finding.required_morphology_classification_types.all()
        ]
        return required_classification_types
    
class FindingMorphologyClassificationManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class FindingMorphologyClassification(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True)
    name_en = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    classification_type = models.ForeignKey(FindingMorphologyClassificationType, on_delete=models.CASCADE)

    findings = models.ManyToManyField('Finding', blank=True, related_name='morphology_classifications')
    examinations = models.ManyToManyField('Examination', blank=True, related_name='morphology_classifications')
    finding_types = models.ManyToManyField('FindingType', blank=True, related_name='morphology_classifications')
    

    objects = FindingMorphologyClassificationManager()



    if TYPE_CHECKING:
        from endoreg_db.models import Finding, Examination, FindingType
        classification_type: models.ForeignKey[FindingMorphologyClassificationType]
        findings: models.QuerySet[Finding]
        examinations: models.QuerySet[Examination]
        finding_types: models.QuerySet[FindingType]
        choices: models.QuerySet['FindingMorphologyClassificationChoice']

    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return str(self.name)
    
    def get_choices(self):
        choices: List[FindingMorphologyClassificationChoice] = [_ for _ in self.choices.all()]
        return choices
    
class FindingMorphologyClassificationChoiceManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class FindingMorphologyClassificationChoice(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True)
    name_en = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    classification = models.ForeignKey(
        FindingMorphologyClassification, 
        on_delete=models.CASCADE,
        related_name='choices'
    )
    
    subcategories = models.JSONField(
        default = dict
    )

    numerical_descriptors = models.JSONField(
        default = dict
    )

    objects = FindingMorphologyClassificationChoiceManager()
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        _str = f"{self.name} ({self.classification})"
        return _str

    
        
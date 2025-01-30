from django.db import models
from typing import List

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
        return self.name
    
    @classmethod
    def get_required_classifications_for_finding(cls, finding):
        """
        Returns all required morphology classification types for a given finding.
        """
        from endoreg_db.models import FindingMorphologyClassificationType
        required_classification_types:List[FindingMorphologyClassificationType] = [
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
    
    objects = FindingMorphologyClassificationManager()
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
    
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

    
        
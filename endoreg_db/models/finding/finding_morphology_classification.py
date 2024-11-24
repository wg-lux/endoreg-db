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
    
    subcategories = models.JSONField(blank=True, null=True)
    numerical_descriptors = models.JSONField(blank=True, null=True)

    objects = FindingMorphologyClassificationChoiceManager()
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        _str = f"{self.name} ({self.classification})"
        return _str

    
        
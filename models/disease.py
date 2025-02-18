from django.db import models
from typing import List

class DiseaseManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class Disease(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    subcategories = models.JSONField(default=dict)
    numerical_descriptors = models.JSONField(default=dict)

    objects = DiseaseManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
    
    

        


    
    def get_classifications(self)->List['DiseaseClassification']:
        classifications: List[DiseaseClassification] = [_ for _ in self.disease_classifications.all()]
        return classifications
    
class DiseaseClassificationManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class DiseaseClassification(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    disease = models.ForeignKey(
        Disease, on_delete=models.CASCADE,
        related_name='disease_classifications'
    )

    objects = DiseaseClassificationManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
    
    def get_choices(self)->List['DiseaseClassificationChoice']:
        choices:List[DiseaseClassificationChoice] = [_ for _ in self.disease_classification_choices.all()]
        return choices
    
class DiseaseClassificationChoiceManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class DiseaseClassificationChoice(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    disease_classification = models.ForeignKey(
        DiseaseClassification, on_delete=models.CASCADE,
        related_name='disease_classification_choices'
    )

    objects = DiseaseClassificationChoiceManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
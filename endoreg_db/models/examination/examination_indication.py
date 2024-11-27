from django.db import models
from typing import List

class ExaminationIndicationManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class ExaminationIndication(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    classification = models.ForeignKey(
        'ExaminationIndicationClassification', on_delete=models.CASCADE,
        related_name='indications',
        blank=True, null=True
    )
    examination = models.ForeignKey(
        'Examination', on_delete=models.CASCADE,
        related_name='indications',
        blank=True, null=True
    )
    
    objects = ExaminationIndicationManager()
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
    
    def get_choices(self)->List['ExaminationIndicationClassificationChoice']:
        choices:List[ExaminationIndicationClassificationChoice] = [_ for _ in self.classification.choices.all()]
        return choices
    
class ExaminationIndicationClassificationManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class ExaminationIndicationClassification(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    
    
    objects = ExaminationIndicationClassificationManager()
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
    
    def get_choices(self)->List['ExaminationIndicationClassificationChoice']:
        choices:List[ExaminationIndicationClassificationChoice] = [_ for _ in self.examination_indication_classification_choices.all()]
        return choices

class ExaminationIndicationClassificationChoiceManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class ExaminationIndicationClassificationChoice(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    subcategories = models.JSONField(default=dict)
    numerical_descriptors = models.JSONField(default=dict)
    
    classification = models.ForeignKey(
        ExaminationIndicationClassification, on_delete=models.CASCADE,
        related_name='choices'
    )
    
    objects = ExaminationIndicationClassificationChoiceManager()
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
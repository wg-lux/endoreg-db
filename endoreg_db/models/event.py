from django.db import models
from typing import List

class Event(models.Model):
    """
    A class representing an event.

    Attributes:
        name (str): The name of the event.
        name_de (str): The German name of the event.
        name_en (str): The English name of the event.
        description (str): A description of the event.
    """
    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    event_classification = models.ForeignKey(
        'EventClassification', on_delete=models.CASCADE, related_name='events',
        null=True, blank=True
    )

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
    

class EventClassificationManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class EventClassification(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    
    objects = EventClassificationManager()
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
    
    def get_choices(self)->List['EventClassificationChoice']:
        choices:List[EventClassificationChoice] = [_ for _ in self.event_classification_choices.all()]
        return choices
    
class EventClassificationChoiceManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class EventClassificationChoice(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    subcategories = models.JSONField(default=dict)
    numerical_descriptors = models.JSONField(default=dict)
    
    event_classification = models.ForeignKey(
        EventClassification, on_delete=models.CASCADE,
        related_name='event_classification_choices'
    )
    
    objects = EventClassificationChoiceManager()
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
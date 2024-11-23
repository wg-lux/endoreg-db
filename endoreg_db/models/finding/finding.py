# Class to represent findings of examinations
from django.db import models

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

    objects = FindingManager()

    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
    
    def get_finding_types(self):
        return self.finding_types.all()
    

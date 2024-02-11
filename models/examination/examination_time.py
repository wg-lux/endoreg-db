from django.db import models
from rest_framework import serializers

class ExaminationTimeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class ExaminationTime(models.Model):
    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    start_time = models.TimeField(blank=True, null=True)
    time_types = models.ManyToManyField('ExaminationTimeType', blank=True)
    end_time = models.TimeField(blank=True, null=True)
    objects = ExaminationTimeManager()

    def __str__(self):
        return self.name
    
    def natural_key(self):
        return (self.name,)

    class Meta:
        verbose_name = 'Examination Time'
        verbose_name_plural = 'Examination Times'
        ordering = ['name']


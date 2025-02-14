from django.db import models
from rest_framework import serializers

class ExaminationTimeManager(models.Manager):
    """
    Manager for ExaminationTime with custom query methods.
    """
    def get_by_natural_key(self, name: str) -> "ExaminationTime":
        return self.get(name=name)

class ExaminationTime(models.Model):
    """
    Represents a specific time configuration for examinations.

    Attributes:
        name (str): The unique name of the examination time.
        name_de (str): The German name of the examination time.
        name_en (str): The English name of the examination time.
        start_time (TimeField): The starting time for the examination.
        end_time (TimeField): The ending time for the examination.
        time_types (ManyToManyField): The types associated with this examination time.
    """
    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    start_time = models.TimeField(blank=True, null=True)
    time_types = models.ManyToManyField('ExaminationTimeType', blank=True)
    end_time = models.TimeField(blank=True, null=True)
    objects = ExaminationTimeManager()

    def __str__(self) -> str:
        """
        String representation of the examination time.

        Returns:
            str: The name of the examination time.
        """
        return self.name

    def natural_key(self) -> tuple:
        """
        Returns the natural key for the examination time.

        Returns:
            tuple: The natural key consisting of the name.
        """
        return (self.name,)

    class Meta:
        verbose_name = 'Examination Time'
        verbose_name_plural = 'Examination Times'
        ordering = ['name']


from django.db import models

class UnitManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class Unit(models.Model):
    objects = UnitManager()

    name = models.CharField(max_length=100) # e.g. "Centimeter"
    name_de = models.CharField(max_length=100, blank=True, null=True) # e.g. "Zentimeter"
    name_en = models.CharField(max_length=100, blank=True, null=True) # e.g. "Centimeter"
    description = models.CharField(max_length=100, blank=True, null=True) # e.g. "centimeters", "millimeters", "inches"
    abbreviation = models.CharField(max_length=10, blank=True, null=True) # e.g. "cm", "mm", "in"

    def __str__(self):
        return self.abbreviation
    
    def natural_key(self):
        return (self.name,)
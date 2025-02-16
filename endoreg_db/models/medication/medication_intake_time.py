'''Model for medication intake time'''
from django.db import models


class MedicationIntakeTimeManager(models.Manager):
    '''Manager for the medication intake time model.'''
    def get_by_natural_key(self, name):
        '''Retrieve a medication intake time by its natural key.'''
        return self.get(name=name)
    
class MedicationIntakeTime(models.Model):
    '''Model representing a medication intake time.'''
    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    repeats = models.CharField(max_length=20, default = "daily")
    time = models.TimeField()

    objects = MedicationIntakeTimeManager()

    def natural_key(self):
        '''Return the natural key for the medication intake time.'''
        return (self.name,)
    
    def __str__(self):
        return self.name + " at " + str(self.time) + " (" + self.repeats + ")"

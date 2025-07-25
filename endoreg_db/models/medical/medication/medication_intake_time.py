'''Model for medication intake time'''
from django.db import models


class MedicationIntakeTimeManager(models.Manager):
    '''Manager for the medication intake time model.'''
    def get_by_natural_key(self, name):
        '''Retrieve a medication intake time by its natural key.'''
        return self.get(name=name)
    
class MedicationIntakeTime(models.Model):
    '''Model representing a medication intake time.'''
    name = models.CharField(max_length=255, unique=True)  # Made unique=True 
    repeats = models.CharField(max_length=20, default = "daily")
    time = models.TimeField()

    objects = MedicationIntakeTimeManager()

    @classmethod
    def dm(cls):
        '''Return the daily morning intake time object.'''
        return cls.objects.get(name='daily-morning')
    
    @classmethod
    def dno(cls):
        '''Return the daily noon intake time object.'''
        return cls.objects.get(name='daily-noon')

    @classmethod
    def de(cls):
        '''Return the daily evening intake time object.'''
        return cls.objects.get(name='daily-evening')
    
    @classmethod
    def dn(cls):
        '''Return the daily night intake time object.'''
        return cls.objects.get(name='daily-night')

    def natural_key(self):
        '''Return the natural key for the medication intake time.'''
        return (self.name,)
    
    def __str__(self):
        return self.name + " at " + str(self.time) + " (" + self.repeats + ")"

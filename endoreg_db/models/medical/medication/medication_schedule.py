'''Model for the medication schedule.'''
from typing import List
from django.db import models

class MedicationScheduleManager(models.Manager):
    '''Manager for the medication schedule model.'''
    def get_by_natural_key(self, name):
        '''Retrieve a medication schedule by its natural key.'''
        return self.get(name=name)
    
class MedicationSchedule(models.Model):
    '''Model representing a medication schedule.'''
    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    medication = models.ForeignKey("Medication", on_delete=models.CASCADE)
    unit = models.ForeignKey("Unit", on_delete=models.CASCADE)
    therapy_duration_d = models.FloatField(blank=True, null=True)
    dose = models.FloatField()
    intake_times = models.ManyToManyField(
        "MedicationIntakeTime", 
    )

    objects = MedicationScheduleManager()

    def natural_key(self):
        '''Return the natural key for the medication schedule.'''
        return (self.name,)
    
    def __str__(self):
        return str(self.name)
    
    def get_intake_times(self) -> List["MedicationIntakeTime"]:
        '''Return a list of all intake times for this medication schedule.'''
        return [_ for _ in self.intake_times.all()] # type: ignore # pylint: disable=E1101
    
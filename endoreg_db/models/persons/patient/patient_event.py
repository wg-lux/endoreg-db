from django.db import models
from .patient import Patient

class PatientEvent(models.Model):
    """
    A class representing an event for a patient.

    Attributes:
        patient (Patient): The patient associated with this event.
        date (datetime.date): The date of the event.
        description (str): A description of the event.
    """
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE)
    event = models.ForeignKey("Event", on_delete=models.CASCADE)
    date_start = models.DateField()
    date_end = models.DateField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    last_update = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return str(self.date_start) + ": " + self.event

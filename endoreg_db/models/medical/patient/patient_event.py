from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...administration.person.patient.patient import Patient
    from ..event import Event, EventClassificationChoice

class PatientEvent(models.Model):
    """
    A class representing an event for a patient.

    Attributes:
        patient (Patient): The patient associated with this event.
        date (datetime.date): The date of the event.
        description (str): A description of the event.
    """
    patient = models.ForeignKey("Patient", on_delete=models.CASCADE)
    event = models.ForeignKey("Event", on_delete=models.CASCADE)
    date_start = models.DateField()
    date_end = models.DateField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    classification_choice = models.ForeignKey(
        "EventClassificationChoice", on_delete=models.CASCADE, blank=True, null=True
    )

    subcategories = models.JSONField(default=dict)
    numerical_descriptors = models.JSONField(default=dict)

    last_update = models.DateTimeField(auto_now=True)
    
    if TYPE_CHECKING:
        patient: "Patient"
        event: "Event"
        classification_choice: "EventClassificationChoice"

    def __str__(self):
        return str(self.date_start) + ": " + self.event.name
    
    def set_subcategories_from_classification_choice(self):
        """
        Sets the subcategories for this event from the classification choice.
        """
        if self.classification_choice:
            self.subcategories = self.classification_choice.subcategories
            self.save()
        
        return self.subcategories
    
    def set_numerical_descriptors_from_classification_choice(self):
        """
        Sets the numerical descriptors for this event from the classification choice.
        """
        if self.classification_choice:
            self.numerical_descriptors = self.classification_choice.numerical_descriptors
            self.save()
        
        return self.numerical_descriptors

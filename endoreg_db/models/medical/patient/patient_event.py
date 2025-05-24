from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...administration.person.patient.patient import Patient
    from ..event import Event, EventClassificationChoice

class PatientEvent(models.Model):
    """
    Represents a specific event occurrence for a patient, potentially with classification details.

    Links a patient to an event type, dates, description, and optional classification choices,
    subcategories, and numerical descriptors.
    """
    patient:models.ForeignKey["Patient"] = models.ForeignKey(
        "Patient", on_delete=models.CASCADE,
        related_name="events"
    )
    event:models.ForeignKey["Event"] = models.ForeignKey(
        "Event", on_delete=models.CASCADE,
        related_name="patient_events"
    )
    date_start = models.DateField()
    date_end = models.DateField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    classification_choice:models.ForeignKey["EventClassificationChoice"] = models.ForeignKey(
        "EventClassificationChoice", on_delete=models.CASCADE, blank=True, null=True
    )

    subcategories = models.JSONField(default=dict)
    numerical_descriptors = models.JSONField(default=dict)

    last_update = models.DateTimeField(auto_now=True)

    def __str__(self):
        """
        Returns a string representation of the patient event with its start date and event name.
        """
        return str(self.date_start) + ": " + self.event.name
    
    def set_subcategories_from_classification_choice(self):
        """Copies subcategory definitions from the linked classification choice."""
        if self.classification_choice:
            self.subcategories = self.classification_choice.subcategories
            self.save()
        
        return self.subcategories
    
    def set_numerical_descriptors_from_classification_choice(self):
        """Copies numerical descriptor definitions from the linked classification choice."""
        if self.classification_choice:
            self.numerical_descriptors = self.classification_choice.numerical_descriptors
            self.save()
        
        return self.numerical_descriptors

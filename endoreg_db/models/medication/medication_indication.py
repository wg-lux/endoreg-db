"""Model for medication indication."""

from django.db import models


class MedicationIndicationManager(models.Manager):
    """Manager for the medication indication model."""

    def get_by_natural_key(self, name):
        """Retrieve a medication indication by its natural key."""
        return self.get(name=name)


class MedicationIndication(models.Model):
    """Model representing a medication indication."""

    name = models.CharField(max_length=255, unique=True)
    indication_type = models.ForeignKey(
        "MedicationIndicationType",
        on_delete=models.CASCADE,
        related_name="medication_indications",
    )
    medication_schedules = models.ManyToManyField(
        "MedicationSchedule",
    )
    diseases = models.ManyToManyField("Disease")
    events = models.ManyToManyField("Event")
    disease_classification_choices = models.ManyToManyField(
        "DiseaseClassificationChoice"
    )
    sources = models.ManyToManyField("InformationSource")

    def get_indication_links(self) -> dict:
        """Return a dictionary of all linked objects for this medication indication."""
        links = {
            "medication_schedules": self.medication_schedules,
            "diseases": self.diseases,
            "events": self.events,
            "disease_classification_choices": self.disease_classification_choices,
        }
        return links

    objects = MedicationIndicationManager()

    def natural_key(self):
        """Return the natural key for the medication indication."""
        return (self.name,)

    def __str__(self):
        return str(self.name)

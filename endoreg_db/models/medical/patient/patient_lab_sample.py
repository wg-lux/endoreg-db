from django.db import models
from typing import TYPE_CHECKING
from datetime import datetime as dt, timezone

if TYPE_CHECKING:
    from ...administration.person.patient import Patient
    from .patient_lab_value import PatientLabValue

DEFAULT_PATIENT_LAB_SAMPLE_TYPE_NAME = "generic"

class PatientLabSampleTypeManager(models.Manager):
    """Manager for PatientLabSampleType with natural key support."""
    def get_by_natural_key(self, name):
        """Retrieves a PatientLabSampleType instance by its natural key (name)."""
        return self.get(name=name)

class PatientLabSampleType(models.Model):
    """
    Represents the type of a patient lab sample (e.g., Blood, Urine).

    Attributes:
        name (str): The name of the patient lab sample type.
        name_de (str): The German name of the patient lab sample type.
        name_en (str): The English name of the patient lab sample type.
        description (str): A description of the patient lab sample type.
    """
    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, null=True)
    name_en = models.CharField(max_length=255, null=True)
    description = models.TextField(blank=True, null=True)

    objects = PatientLabSampleTypeManager()

    def natural_key(self):
        """Returns the natural key (name) as a tuple."""
        return (self.name,)

    def __str__(self):
        """Returns the name of the sample type."""
        return str(self.name)
    
    @classmethod
    def get_default_sample_type(cls):
        """Gets or creates the default patient lab sample type ('default')."""
        return cls.objects.get_or_create(name="default")[0]

class PatientLabSample(models.Model):
    """
    Represents a specific lab sample taken from a patient at a certain date and time.

    Links to the patient, sample type, and associated lab values.

    Attributes:
        patient (Patient): The patient to which the lab sample belongs.
        sample_type (PatientLabSampleType): The type of the lab sample.
        date (datetime): The date of the lab sample.
        values (PatientLabValue; One2Many): The value of the lab sample.
    """
    patient = models.ForeignKey("Patient", on_delete=models.CASCADE, related_name="lab_samples")
    sample_type = models.ForeignKey("PatientLabSampleType", on_delete=models.CASCADE)
    date = models.DateTimeField()

    if TYPE_CHECKING:
        patient: "Patient"
        sample_type: "PatientLabSampleType"
        values: models.QuerySet["PatientLabValue"]

    def __str__(self):
        """Returns a string representation including patient, type, and date."""
        formatted_datetime = self.date.strftime('%Y-%m-%d %H:%M')
        return f"{self.patient} - {self.sample_type} - {formatted_datetime} ()"
    
    def get_values(self):
        """Returns all PatientLabValue instances associated with this sample."""
        return self.values.all()
    
    @classmethod
    def create_by_patient(cls, patient=None, sample_type=None, date=None, save = True):
        """
        Creates a new patient lab sample for a given patient.

        Uses default type and current time if not provided.

        Args:
            patient (Patient): The patient to which the lab sample belongs.
            sample_type (PatientLabSampleType): The type of the lab sample.
            date (datetime): The date of the lab sample.
            save (bool): Whether to save the instance after creation.

        Returns:
            PatientLabSample: The new patient lab sample.
        """
        from warnings import warn

        if not patient:
            warn("No patient given. Cannot create patient lab sample.")
            return None
        if not sample_type:
            sample_type = PatientLabSampleType.get_default_sample_type()
        else:
            sample_type = PatientLabSampleType.objects.get(name=sample_type)
        if not date:
            date = dt.now(timezone.utc)

        patient_lab_sample = cls.objects.create(
            patient=patient,
            sample_type=sample_type,
            date=date
        )

        if save:
            patient_lab_sample.save()

        return patient_lab_sample





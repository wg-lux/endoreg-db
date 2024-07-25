from django.db import models

DEFAULT_PATIENT_LAB_SAMPLE_TYPE_NAME = "generic"

class PatientLabSampleTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class PatientLabSampleType(models.Model):
    """
    A class representing a patient lab sample type.

    Attributes:
        name (str): The name of the patient lab sample type.
        description (str): A description of the patient lab sample type.

    """
    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, null=True)
    name_en = models.CharField(max_length=255, null=True)
    description = models.TextField(blank=True, null=True)

    objects = PatientLabSampleTypeManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
    
    @classmethod
    def get_default_sample_type(cls):
        """
        Get the default patient lab sample type.

        Returns:
            PatientLabSampleType: The default patient lab sample type.

        """
        return cls.objects.get_or_create(name="default")[0]
    
from datetime import datetime as dt    
from datetime import timezone
class PatientLabSample(models.Model):
    """
    A class representing a patient lab sample.

    Attributes:
        patient (Patient): The patient to which the lab sample belongs.
        sample_type (PatientLabSampleType): The type of the lab sample.
        date (datetime): The date of the lab sample.
        values (PatientLabValue; One2Many): The value of the lab sample.
        unit (Unit): The unit of the lab sample.

    """
    patient = models.ForeignKey("Patient", on_delete=models.CASCADE, related_name="lab_samples")
    sample_type = models.ForeignKey("PatientLabSampleType", on_delete=models.CASCADE)
    date = models.DateTimeField()

    def __str__(self):
        return f"{self.patient} - {self.sample_type} - {self.date} ()"
    
    def get_values(self):
        return self.values
    
    @classmethod
    def create_by_patient(cls, patient=None, sample_type=None, date=None, save = True):
        """
        Create a new patient lab sample by patient.

        Args:
            patient (Patient): The patient to which the lab sample belongs.
            sample_type (PatientLabSampleType): The type of the lab sample.
            date (datetime): The date of the lab sample.

        Returns:
            PatientLabSample: The new patient lab sample.

        """
        from endoreg_db.models.persons.patient import Patient, PatientLabSampleType
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


    
    

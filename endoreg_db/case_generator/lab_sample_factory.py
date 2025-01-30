from datetime import datetime, timezone
from endoreg_db.models import Patient, PatientLabSample, PatientLabSampleType

class LabSampleFactory:
    """
    Provides methods to generate lab samples.
    """

    def __init__(self):
        """
        Initializes the LabSampleFactory.
        """
        pass

    def create_generic_lab_sample(self, patient: Patient):
        """
        Generates a generic lab sample for a given patient.

        Args:
            patient (Patient): The patient for whom the lab sample is generated.

        Returns:
            PatientLabSample: The created lab sample instance.
        """
        sample_type = PatientLabSampleType.objects.get(name="generic")

        lab_sample = PatientLabSample.objects.create(
            patient=patient,
            sample_type=sample_type,
            date=datetime.now(tz=timezone.utc)
        )

        return lab_sample
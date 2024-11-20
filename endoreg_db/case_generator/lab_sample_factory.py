from datetime import datetime, timezone
from endoreg_db.models import Patient, PatientLabSample, PatientLabSampleType

class LabSampleFactory:
    """
    Provides methods to generate lab samples.
    """
    def __init__(self):
        pass

    def generic_lab_sample(self, patient:Patient):
        """
        Generates a lab sample.
        """
        sample_type = PatientLabSampleType.objects.get(name="generic")
        
        patient_lab_sample = PatientLabSample.objects.create(
            patient=patient,
            sample_type=sample_type,
            date=datetime.now(tz=timezone.utc)
        )

        return patient_lab_sample
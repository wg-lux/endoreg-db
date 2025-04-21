from django.db import models
from typing import TYPE_CHECKING
from datetime import datetime as dt

if TYPE_CHECKING:
    from ...administration.person.patient import Patient
    from .patient_medication import PatientMedication
    from ..medication import MedicationSchedule

class PatientMedicationSchedule(models.Model):
    """
    Represents a collection of medications associated with a patient, forming their schedule.
    """
    patient = models.ForeignKey("Patient", on_delete= models.CASCADE)
    medication = models.ManyToManyField(
        'PatientMedication', 
        related_name='patient_medication_schedules',
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    if TYPE_CHECKING:
        patient: "Patient"
        medication: models.QuerySet["PatientMedication"]

    def __str__(self):
        """Returns a string representation including the patient and associated medications."""
        return f'{self.patient} - {self.medication.all()}'
    
    @classmethod
    def create_by_patient_and_indication_type(cls, patient, indication_type):
        """Creates a schedule and adds a medication based on a random indication of a given type."""
        from ..medication import MedicationIndicationType
        from .patient_medication import PatientMedication

        medication_indication = MedicationIndicationType.get_random_indication_by_type(name=indication_type)

        patient_medication_schedule = cls.objects.create(patient=patient)
        patient_medication_schedule.save()

        patient_medication = PatientMedication.create_by_patient_and_indication(patient, medication_indication)
        patient_medication_schedule.medication.add(patient_medication)
        patient_medication_schedule.save()

        return patient_medication_schedule
    
    @classmethod
    def create_by_patient_and_indication(cls, patient, medication_indication):
        """Creates a schedule and adds a medication based on a specific indication."""
        from ..medication import MedicationIndication
        from .patient_medication import PatientMedication
        from ...administration.person.patient import Patient


        assert isinstance(medication_indication, MedicationIndication)
        assert isinstance(patient, Patient)
        patient_medication_schedule = cls.objects.create(patient=patient)
        patient_medication_schedule.save()

        patient_medication = PatientMedication.create_by_patient_and_indication(patient, medication_indication)
        patient_medication_schedule.medication.add(patient_medication)
        patient_medication_schedule.save()

        return patient_medication_schedule
    
    def create_patient_medication_from_medication_schedule(
            self,
            medication_schedule: "MedicationSchedule",
            medication_indication=None,
            start_date=None,
        ):
        """Creates and adds a PatientMedication based on a generic MedicationSchedule template."""
        
        from .patient_medication import PatientMedication
        
        if not start_date:
            start_date = dt.now()

        drug = medication_schedule.medication
        unit = medication_schedule.unit
        dosage = medication_schedule.dose
        intake_times = medication_schedule.get_intake_times()

        patient_medication = PatientMedication.objects.create(
            patient=self.patient,
            medication=drug,
            medication_indication=medication_indication,
            unit=unit,
            dosage=dosage
        )

        patient_medication.intake_times.set(intake_times)
        patient_medication.save()

        self.medication.add(patient_medication)
        self.save()  

        return patient_medication
    
    
    def get_patient_medication(self):
        """Returns all PatientMedication instances associated with this schedule."""
        return self.medication.all()
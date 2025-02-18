from django.db import models

class PatientMedicationSchedule(models.Model):
    patient = models.ForeignKey("Patient", on_delete= models.CASCADE)
    medication = models.ManyToManyField(
        'PatientMedication', 
        related_name='patient_medication_schedules',
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.patient} - {self.medication.all()}'
    
    @classmethod
    def create_by_patient_and_indication_type(cls, patient, indication_type):
        from endoreg_db.models import MedicationIndicationType, PatientMedication

        medication_indication = MedicationIndicationType.get_random_indication_by_type(name=indication_type)

        patient_medication_schedule = cls.objects.create(patient=patient)
        patient_medication_schedule.save()

        patient_medication = PatientMedication.create_by_patient_and_indication(patient, medication_indication)
        patient_medication_schedule.medication.add(patient_medication)
        patient_medication_schedule.save()

        return patient_medication_schedule
    
    @classmethod
    def create_by_patient_and_indication(cls, patient, medication_indication):
        from endoreg_db.models import (
            MedicationIndication, PatientMedication,
            Patient
        )

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
            medication_schedule,
            medication_indication=None,
            start_date=None,
        ):
        
        from endoreg_db.models import MedicationSchedule, PatientMedication
        from datetime import datetime as dt

        assert isinstance(medication_schedule, MedicationSchedule)

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
        return self.medication.all()
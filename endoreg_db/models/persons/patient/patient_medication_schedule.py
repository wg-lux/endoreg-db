from django.db import models

class PatientMedicationSchedule(models.Model):
    patient = models.ForeignKey("Patient", on_delete= models.CASCADE)
    medication = models.ManyToManyField(
        'PatientMedication', related_name='patient_medication_schedules'
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
from django.db import models
from django.utils.translation import gettext_lazy as _

class PatientMedication(models.Model):
    patient = models.ForeignKey("Patient", on_delete= models.CASCADE)
    medication_indication = models.ForeignKey(
        "MedicationIndication", on_delete=models.CASCADE,
        related_name="patient_medications", null=True
    )

    medication_schedules = models.ManyToManyField(
        'MedicationSchedule'
    )
    unit = models.ForeignKey('Unit', on_delete=models.CASCADE)
    dosage = models.JSONField()
    active = models.BooleanField(default=True)

    objects = models.Manager()

    class Meta:
        verbose_name = _('Patient Medication')
        verbose_name_plural = _('Patient Medications')

    @classmethod
    def create_by_patient_and_indication(cls, patient, medication_indication):
        from endoreg_db.models import MedicationIndication
        medication_indication: MedicationIndication
        patient_medication = cls.objects.create(patient=patient, medication_indication=medication_indication)
        patient_medication.save()
        patient_medication.set_schedules_from_indication()
        
        return patient_medication

    def set_schedules_from_indication(self):
        schedules = self.medication_indication.medication_schedules.all()
        self.medication_schedules.set(schedules)
        self.save()

    def __str__(self):
        indication = self.medication_indication
        schedules = self.medication_schedules.all()
        return f'{indication} - {schedules}'
    
    
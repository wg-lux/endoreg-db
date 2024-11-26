from django.db import models
from django.utils.translation import gettext_lazy as _

class PatientMedication(models.Model):
    patient = models.ForeignKey("Patient", on_delete= models.CASCADE)
    medication_indication = models.ForeignKey(
        "MedicationIndication", on_delete=models.CASCADE,
        related_name="patient_medications", null=True
    )

    medication = models.ForeignKey(
        'Medication', on_delete=models.CASCADE,
        blank=True,
        related_name='patient_medications'
    )

    intake_times = models.ManyToManyField(
        'MedicationIntakeTime', 
        related_name='patient_medications',
        blank=True,
    )

    unit = models.ForeignKey(
        'Unit', on_delete=models.CASCADE,
        null=True, blank=True
    )
    dosage = models.JSONField(
        null=True, blank=True
    )
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
        
        return patient_medication


    def __str__(self):
        intake_times = self.intake_times.all()
        out = f"{self.medication} (Indication {self.medication_indication}) - "
        out += f"{self.dosage} - {self.unit} - "


        for intake_time in intake_times:
            out += f"{intake_time} - "

        return out
    
    
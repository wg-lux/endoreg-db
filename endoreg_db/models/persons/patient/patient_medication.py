from django.db import models
from django.utils.translation import gettext_lazy as _

class PatientMedication(models.Model):
    patient = models.ForeignKey("Patient", on_delete= models.CASCADE)
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

    def __str__(self):
        return self.name
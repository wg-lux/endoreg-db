from django.db import models
from django.utils.translation import gettext_lazy as _

class PatientMedication(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    medication = models.ForeignKey('Medication', on_delete=models.CASCADE)
    unit = models.ForeignKey('Unit', on_delete=models.CASCADE)
    dosage = models.JSONField()
    active = models.BooleanField(default=True)

    objects = models.Manager()

    class Meta:
        verbose_name = _('Patient Medication')
        verbose_name_plural = _('Patient Medications')

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
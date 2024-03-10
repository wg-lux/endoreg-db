from django.db import models

class SensitiveMeta(models.Model):
    examination_date = models.DateField(blank=True, null=True)
    patient_first_name = models.CharField(max_length=255, blank=True, null=True)
    patient_last_name = models.CharField(max_length=255, blank=True, null=True)
    patient_dob = models.DateField(blank=True, null=True)
    endoscope_type = models.CharField(max_length=255, blank=True, null=True)
    endoscope_sn = models.CharField(max_length=255, blank=True, null=True)

    @classmethod
    def create_from_dict(cls, data: dict):
        return cls.objects.create(**data)

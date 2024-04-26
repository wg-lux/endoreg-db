from django.db import models

class MedicationManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class Medication(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    default_unit = models.ForeignKey('Unit', on_delete=models.CASCADE)

    objects = MedicationManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
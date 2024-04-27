from django.db import models

class MedicationManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class Medication(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    adapt_to_renal_function = models.BooleanField(default = False)
    adapt_to_liver_function = models.BooleanField(default=False)
    default_unit = models.ForeignKey('Unit', on_delete=models.CASCADE)

    objects = MedicationManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
    
class MedicationSchedule(models.Model):
    name = models.CharField(max_length=255)
    medication = models.ForeignKey("Medication", on_delete=models.CASCADE)
    unit = models.ForeignKey("Unit", on_delete=models.CASCADE)
    intake_times = models.ManyToManyField(
        "MedicationIntakeTime", 
    )
    

class MedicationIntakeTime(models.Model):
    repeats = models.CharField(max_length=20, default = "daily")
    time = models.TimeField()
    unit = models.ForeignKey("Unit", on_delete=models.CASCADE)
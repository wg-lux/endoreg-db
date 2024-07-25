from django.db import models

class MedicationManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class Medication(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    adapt_to_renal_function = models.BooleanField(default = False)
    adapt_to_hepatic_function = models.BooleanField(default=False)
    adapt_to_indication = models.BooleanField(default=False)
    adapt_to_age = models.BooleanField(default=False)
    adapt_to_weight = models.BooleanField(default=False)
    adapt_to_risk = models.BooleanField(default=False)
    default_unit = models.ForeignKey('Unit', on_delete=models.CASCADE)


    objects = MedicationManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
    
class MedicationScheduleManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class MedicationSchedule(models.Model):
    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    medication = models.ForeignKey("Medication", on_delete=models.CASCADE)
    unit = models.ForeignKey("Unit", on_delete=models.CASCADE)
    therapy_duration_d = models.FloatField(blank=True, null=True)
    dose = models.FloatField()
    intake_times = models.ManyToManyField(
        "MedicationIntakeTime", 
    )

    objects = MedicationScheduleManager()

    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
    
    
class MedicationIntakeTimeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class MedicationIntakeTime(models.Model):
    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    repeats = models.CharField(max_length=20, default = "daily")
    time = models.TimeField()

    objects = MedicationIntakeTimeManager()

    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name

# IMPLEMENT MEDICATION INDICATION TYPE
class MedicationIndicationTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class MedicationIndicationType(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    objects = MedicationIndicationTypeManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
    
    @classmethod
    def get_random_indication_by_type(cls, name) -> "MedicationIndication":
        return cls.objects.get(name=name).medication_indications.order_by('?').first()
    

    def get_random_medication_indication(self):
        from endoreg_db.models import MedicationIndication
        return MedicationIndication.objects.filter(indication_type=self).order_by('?').first()


class MedicationIndicationManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class MedicationIndication(models.Model):
    name = models.CharField(max_length=255, unique=True)
    indication_type = models.ForeignKey(
        "MedicationIndicationType", on_delete=models.CASCADE, related_name="medication_indications"
    )
    medication_schedules = models.ManyToManyField(
        "MedicationSchedule"
    )
    diseases = models.ManyToManyField(
        "Disease"
    )
    events = models.ManyToManyField(
        "Event"
    )
    classification_choices = models.ManyToManyField(
        "DiseaseClassificationChoice"
    )
    sources = models.ManyToManyField(
        "InformationSource"
    )

    def get_indication_links(self):
        links = {
            "medication_schedules": self.medication_schedules,
            "diseases": self.diseases,
            "events": self.events,
            "classification_choices": self.classification_choices
        }

    objects = MedicationIndicationManager()

    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
    
    def create_patient_medication_schedules(self, patient):
        from endoreg_db.models import PatientMedicationSchedule
        for medication_schedule in self.medication_schedules.all():
            PatientMedicationSchedule.objects.create(
                patient=patient,
                medication_schedule=medication_schedule
            )

from django.db import models

class PatientLabValue(models.Model):
    """
    A class representing a patient lab value.

    Attributes:
        patient (Patient): The patient.
        lab_value (LabValue): The lab value.
        value (float): The value of the lab value.
        date (datetime): The date of the lab value.
    """
    patient = models.ForeignKey('Patient', on_delete=models.CASCADE)
    lab_value = models.ForeignKey('LabValue', on_delete=models.CASCADE)
    value = models.FloatField(blank=True, null=True)
    value_str = models.CharField(max_length=255, blank=True, null=True)
    date = models.DateTimeField()
    normal_range = models.JSONField(
        default = {}
    )
    unit = models.ForeignKey('Unit', on_delete=models.CASCADE, blank=True, null=True)

    def __str__(self):
        return f'{self.patient} - {self.lab_value} - {self.value} - {self.date}'
    
    def set_min_norm_value(self, value, save = True):
        self.normal_range["min"] = value
        if save:
            self.save()

    def set_max_norm_value(self, value, save = True):
        self.normal_range["max"] = value
        if save:
            self.save()

    def set_unit_from_default(self):
        age = self.patient.age
        gender = self.patient.gender
        min_value, max_value = self.lab_value.get_normal_range(age=age, gender=gender)
        self.set_min_norm_value(min_value, save = False)
        self.set_max_norm_value(max_value, save = False)
        self.save()


    def set_unit_from_default(self):
        self.unit = self.lab_value.default_unit


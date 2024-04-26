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
    unit = models.ForeignKey('Unit', on_delete=models.CASCADE, blank=True, null=True)

    def __str__(self):
        return f'{self.patient} - {self.lab_value} - {self.value} - {self.date}'
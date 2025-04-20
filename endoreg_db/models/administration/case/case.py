from django.db import models

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from endoreg_db.models import Patient, PatientExamination
class Case(models.Model):
    """
    A class representing a case.

    Attributes:
        name (str): The name of the case.
        description (str): A description of the case.
        case_template (CaseTemplate): The case template of the case.
        patient (Patient): The patient of the case.
        start_date (datetime): The start date of the case.
        end_date (datetime): The end date of the case.
        is_active (bool): A flag indicating whether the case is active.
        is_closed (bool): A flag indicating whether the case is closed.
        is_deleted (bool): A flag indicating whether the case is deleted.
        created_at (datetime): The creation date of the case.
        updated_at (datetime): The last update date of the case.

    """
    patient = models.ForeignKey('Patient', on_delete=models.CASCADE, related_name='cases')
    patient_examinations = models.ManyToManyField('PatientExamination', related_name='cases')
    hash = models.CharField(max_length=255, blank=True, null=True)

    start_date = models.DateTimeField()
    end_date = models.DateTimeField(null=True)
    is_active = models.BooleanField(default=True)
    is_closed = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    if TYPE_CHECKING:
        patient: "Patient"
        patient_examinations: models.QuerySet["PatientExamination"]

    def __str__(self):
        string_representation = f"Case {self.pk} ({self.patient})"
        if self.start_date:
            string_representation += f" - {self.start_date.strftime('%Y-%m-%d')}"
        if self.end_date:
            string_representation += f" - {self.end_date.strftime('%Y-%m-%d')}"
        return string_representation

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from ..examination import (
        ExaminationIndication,
        ExaminationIndicationClassificationChoice,
    )
    from .patient_examination import PatientExamination


class PatientExaminationIndication(models.Model):
    """A model to store the indication for a patient examination."""

    patient_examination = models.ForeignKey("PatientExamination", on_delete=models.CASCADE, related_name="indications")
    examination_indication = models.ForeignKey("ExaminationIndication", on_delete=models.CASCADE)
    indication_choice = models.ForeignKey("ExaminationIndicationClassificationChoice", on_delete=models.CASCADE, blank=True, null=True)

    if TYPE_CHECKING:
        patient_examination: models.ForeignKey["PatientExamination"]
        examination_indication: models.ForeignKey["ExaminationIndication"]
        indication_choice: models.ForeignKey["ExaminationIndicationClassificationChoice|None"]

    def __str__(self):
        return f"{self.patient_examination} - {self.examination_indication}"

    def get_examination(self):
        pe = self.get_patient_examination()
        e = pe.examination

        return e

    def get_patient_examination(self):
        pe = self.patient_examination
        return pe

    def get_patient(self):
        pe = self.get_patient_examination()
        patient = pe.patient
        return patient

    def get_choices(self):
        examination_indication = self.examination_indication
        choices = [_ for _ in examination_indication.get_choices()]
        return choices

from __future__ import annotations
from typing import TYPE_CHECKING, cast

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models.medical.examination.examination import Examination
    from endoreg_db.models.medical.examination.examination_indication import (
        ExaminationIndication,
        ExaminationIndicationClassificationChoice,
    )
    from endoreg_db.models.administration.person.patient.patient import Patient
    from endoreg_db.models.medical.patient.patient_examination import (
        PatientExamination,
    )


class PatientExaminationIndication(models.Model):
    """A model to store the indication for a patient examination."""

    patient_examination: models.ForeignKey[
        "PatientExamination",
        "PatientExamination",
    ] = models.ForeignKey(
        "PatientExamination", on_delete=models.CASCADE, related_name="indications"
    )
    examination_indication: models.ForeignKey[
        "ExaminationIndication",
        "ExaminationIndication",
    ] = models.ForeignKey("ExaminationIndication", on_delete=models.CASCADE)
    indication_choice: models.ForeignKey[
        "ExaminationIndicationClassificationChoice | None",
        "ExaminationIndicationClassificationChoice | None",
    ] = models.ForeignKey(
        "ExaminationIndicationClassificationChoice",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )

    if TYPE_CHECKING:
        pass

    def __str__(self) -> str:
        return f"{self.patient_examination} - {self.examination_indication}"

    def get_examination(self) -> "Examination":
        pe = self.get_patient_examination()
        return pe.examination_safe

    def get_patient_examination(self) -> "PatientExamination":
        pe = self.patient_examination
        return pe

    def get_patient(self) -> "Patient":
        pe = self.get_patient_examination()
        return cast("Patient", pe.patient)

    def get_choices(self) -> list["ExaminationIndicationClassificationChoice"]:
        examination_indication = self.examination_indication
        choices = [
            choice
            for classification in examination_indication.classifications.all()
            for choice in classification.choices.all()
        ]
        return choices

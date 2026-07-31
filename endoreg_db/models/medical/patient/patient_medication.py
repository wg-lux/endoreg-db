from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Any

from django.db import models
from django.core.exceptions import ValidationError
from endoreg_db.schemas import validate_patient_medication_dosage

# Added imports for type hints
if TYPE_CHECKING:
    from endoreg_db.models.administration.person.patient.patient import Patient
    from endoreg_db.models.medical.medication.medication import Medication
    from endoreg_db.models.medical.medication.medication_indication import (
        MedicationIndication,
    )
    from endoreg_db.models.medical.medication.medication_intake_time import (
        MedicationIntakeTime,
    )
    from endoreg_db.utils.links import ModelLinks


class PatientMedication(models.Model):
    """
    Represents a specific medication prescribed or taken by a patient.

    Links a patient to a medication, its indication, dosage, intake times, and unit.
    """

    patient: models.ForeignKey[Any] = models.ForeignKey(
        "Patient", on_delete=models.CASCADE
    )
    medication_indication: models.ForeignKey[
        "MedicationIndication", "MedicationIndication | None"
    ] = models.ForeignKey(
        "MedicationIndication",
        on_delete=models.CASCADE,
        related_name="indication_patient_medications",
        null=True,
    )

    medication: models.ForeignKey[Any] = models.ForeignKey(
        "Medication",
        on_delete=models.CASCADE,
        blank=True,
        related_name="medication_patient_medications",
    )

    intake_times: models.ManyToManyField[
        "MedicationIntakeTime",
        "MedicationIntakeTime",
    ] = models.ManyToManyField(
        "MedicationIntakeTime",
        related_name="intake_time_patient_medications",
        blank=True,
    )

    unit: models.ForeignKey[Any] = models.ForeignKey(
        "Unit", on_delete=models.CASCADE, null=True, blank=True
    )
    dosage: models.JSONField[Any, Any] = models.JSONField(null=True, blank=True)
    active: models.BooleanField[Any, Any] = models.BooleanField(default=True)

    objects: ClassVar[models.Manager["PatientMedication"]] = (  # pyright: ignore[reportIncompatibleVariableOverride]
        models.Manager()
    )

    def clean(self) -> None:
        super().clean()
        try:
            self.dosage = validate_patient_medication_dosage(self.dosage)
        except ValueError as exc:
            raise ValidationError({"dosage": str(exc)}) from exc

    def save(self, *args: object, **kwargs: object) -> None:
        self.clean()
        super().save(*args, **kwargs)

    if TYPE_CHECKING:  # Added type hints block

        @property
        def patient_medications(self) -> models.QuerySet["PatientMedication"]: ...

    @property
    def links(self) -> ModelLinks:
        """
        Returns a ModelLinks object for this PatientMedication instance.
        This is used during linked-model traversal.
        """
        from ....utils.links import ModelLinks

        meds: list[Medication] = []
        if self.medication:
            meds.append(self.medication)

        indications: list[MedicationIndication] = []
        if self.medication_indication:
            indications.append(self.medication_indication)

        intake_times_list: list[MedicationIntakeTime] = list(self.intake_times.all())

        return ModelLinks(
            medications=meds,
            medication_indications=indications,
            medication_intake_times=intake_times_list,
            patient_medications=[self],
        )

    class Meta:
        verbose_name = "Patient Medication"
        verbose_name_plural = "Patient Medications"

    @classmethod
    def create_by_patient_and_indication(
        cls, patient: "Patient", medication_indication: "MedicationIndication"
    ) -> "PatientMedication":
        """Creates a PatientMedication instance linking a patient and an indication."""

        patient_medication = cls.objects.create(
            patient=patient, medication_indication=medication_indication
        )
        patient_medication.save()

        return patient_medication

    def __str__(self) -> str:
        """Returns a string representation including medication, indication, dosage, and intake times."""
        intake_times = self.intake_times.all()
        out = f"{self.medication} (Indication {self.medication_indication}) - "
        out += f"{self.dosage} - {self.unit} - "

        for intake_time in intake_times:
            out += f"{intake_time} - "

        return out

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, Any, Unpack

from django.db import models
from django.core.exceptions import ValidationError
from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from endoreg_db.schemas import validate_patient_medication_dosage
from lx_dtypes.models.ledger.medical.Write import PatientMedicationCreate
from pydantic import ValidationError as PydanticValidationError

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
        null=True,
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
    substances: models.JSONField[
        list[dict[str, str | None]], list[dict[str, str | None]]
    ] = models.JSONField(default=list, blank=True)
    dosing: models.JSONField[
        dict[str, str | int | None], dict[str, str | int | None]
    ] = models.JSONField(default=dict, blank=True)
    pzn: models.CharField[str | None, str | None] = models.CharField(
        max_length=8, null=True, blank=True
    )
    dosage_form: models.TextField[str | None, str | None] = models.TextField(
        null=True, blank=True
    )
    dosage_form_code: models.CharField[str | None, str | None] = models.CharField(
        max_length=10, null=True, blank=True
    )
    unit_code: models.CharField[str | None, str | None] = models.CharField(
        max_length=1, null=True, blank=True
    )
    notes: models.CharField[str | None, str | None] = models.CharField(
        max_length=80, null=True, blank=True
    )
    bound_note: models.CharField[str | None, str | None] = models.CharField(
        max_length=200, null=True, blank=True
    )
    active: models.BooleanField[Any, Any] = models.BooleanField(default=True)
    start_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True, blank=True)
    )
    end_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True, blank=True)
    )

    objects: ClassVar[models.Manager["PatientMedication"]] = (  # pyright: ignore[reportIncompatibleVariableOverride]
        models.Manager()
    )

    def clean(self) -> None:
        super().clean()
        try:
            self.dosage = validate_patient_medication_dosage(self.dosage)
        except ValueError as exc:
            raise ValidationError({"dosage": str(exc)}) from exc
        try:
            PatientMedicationCreate.model_validate(
                {
                    "medication": self.medication.name
                    if self.medication is not None
                    else None,
                    "medication_indication": self.medication_indication.name
                    if self.medication_indication is not None
                    else None,
                    "unit": self.unit.name if self.unit is not None else None,
                    "start_at": self.start_at,
                    "end_at": self.end_at,
                    "dosage": self.dosage,
                    "pzn": self.pzn,
                    "substances": self.substances,
                    "dosing": self.dosing,
                    "dosage_form": self.dosage_form,
                    "dosage_form_code": self.dosage_form_code,
                    "unit_code": self.unit_code,
                    "notes": self.notes,
                    "bound_note": self.bound_note,
                },
                strict=True,
            )
        except PydanticValidationError as exc:
            raise ValidationError(
                {"medication": "Invalid medication fields or period."}
            ) from exc

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
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
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_at__isnull=True)
                | (
                    models.Q(start_at__isnull=False)
                    & models.Q(end_at__gte=models.F("start_at"))
                ),
                name="patient_medication_valid_period",
            )
        ]

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

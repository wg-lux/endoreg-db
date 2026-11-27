"""Examination revision snapshots; original clinical and media rows retain ownership."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Unpack

from django.core.exceptions import ValidationError
from django.db import models
from lx_dtypes.models.contracts.examination_revision import ExaminationRevisionSnapshot
from lx_dtypes.models.contracts.json_types import JsonObject
from pydantic import ValidationError as SchemaError

from endoreg_db.helpers.typing import DjangoModelSaveKwargs

if TYPE_CHECKING:
    from endoreg_db.models.administration.case.case import Case
    from .patient_examination import PatientExamination


class ExaminationRevision(models.Model):
    objects = models.Manager["ExaminationRevision"]()
    patient_examination: models.ForeignKey[PatientExamination] = models.ForeignKey(
        "PatientExamination", on_delete=models.PROTECT, related_name="revisions"
    )
    title: models.CharField[str, str] = models.CharField(max_length=255)
    version: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(
        default=1
    )
    snapshot: models.JSONField[JsonObject, JsonObject] = models.JSONField()
    finalized_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True, blank=True)
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now=True
    )

    def clean(self) -> None:
        super().clean()
        try:
            snapshot = ExaminationRevisionSnapshot.model_validate(self.snapshot)
        except SchemaError as exc:
            raise ValidationError(
                {"snapshot": "Invalid examination revision."}
            ) from exc
        examination = self.patient_examination
        clinical = snapshot.content.examination
        if (
            clinical.patient != str(examination.patient_id)
            or clinical.examination != examination.examination_safe.name
        ):
            raise ValidationError(
                {"snapshot": "Examination identity does not match its owner."}
            )
        if self.pk:
            previous = ExaminationRevision.objects.get(pk=self.pk)
            if previous.finalized_at is not None and (
                self.snapshot != previous.snapshot
                or self.title != previous.title
                or self.version != previous.version
                or self.finalized_at != previous.finalized_at
                or self.patient_examination.pk != previous.patient_examination.pk
            ):
                raise ValidationError(
                    "Finalized examination revisions are immutable; create an alternative."
                )

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.clean()
        super().save(*args, **kwargs)


class CaseExaminationRecord(models.Model):
    """Exactly one selected revision per examination within one case."""

    objects = models.Manager["CaseExaminationRecord"]()
    case: models.ForeignKey[Case] = models.ForeignKey(
        "Case", on_delete=models.CASCADE, related_name="selected_examinations"
    )
    patient_examination: models.ForeignKey[PatientExamination] = models.ForeignKey(
        "PatientExamination", on_delete=models.PROTECT
    )
    revision: models.ForeignKey[ExaminationRevision] = models.ForeignKey(
        ExaminationRevision, on_delete=models.PROTECT
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["case", "patient_examination"],
                name="one_selected_revision_per_case_exam",
            )
        ]

    def clean(self) -> None:
        super().clean()
        if (
            self.revision.finalized_at is None
            or self.revision.patient_examination.pk != self.patient_examination.pk
        ):
            raise ValidationError("Select a finalized revision of this examination.")
        if not self.case.patient_examinations.filter(
            pk=self.patient_examination.pk, patient_id=self.case.patient.pk
        ).exists():
            raise ValidationError(
                "Selected examination must belong to the case patient and graph."
            )

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.clean()
        super().save(*args, **kwargs)

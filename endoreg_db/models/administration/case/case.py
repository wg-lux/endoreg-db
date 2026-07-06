from __future__ import annotations

from datetime import datetime
from types import NoneType
from typing import TYPE_CHECKING, TypeAlias

from django.db import models
from django.utils import timezone

if TYPE_CHECKING:
    from ...administration.person.patient.patient import Patient
    from ...medical.patient.patient_examination import PatientExamination

NoCaseEndDate: TypeAlias = NoneType
CaseEndDate: TypeAlias = datetime | NoCaseEndDate


class Case(models.Model):
    """
    Represents a clinical case, linking a patient to one or more examinations over a specific period.

    Attributes:
        patient (Patient): The patient associated with this case.
        patient_examinations (QuerySet[PatientExamination]): Examinations included in this case.
        hash (str): A hash value for the case when configured.
        start_date (datetime): The start date and time of the case.
        end_date (datetime): The end date and time of the case when closed.
        is_active (bool): Indicates if the case is currently active.
        is_closed (bool): Indicates if the case has been closed.
        is_deleted (bool): Indicates if the case is marked as deleted.
        created_at (datetime): Timestamp of case creation.
        updated_at (datetime): Timestamp of last case update.
    """

    patient: models.ForeignKey[Patient] = models.ForeignKey(
        "Patient",
        on_delete=models.CASCADE,
        related_name="cases",
        help_text="The patient associated with this case.",
        db_index=True,
    )
    patient_examinations: "models.ManyToManyField[PatientExamination, PatientExamination]" = models.ManyToManyField(
        "PatientExamination",
        related_name="cases",
        help_text="The examinations included in this case.",
    )
    hash: models.CharField[str | None] = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="A hash value associated with the case when configured.",
    )

    start_date: models.DateTimeField[datetime] = models.DateTimeField(
        help_text="The start date and time of the case.", db_index=True
    )
    end_date: models.DateTimeField[CaseEndDate | None] = models.DateTimeField(
        null=True, blank=True, help_text="The end date and time of the case."
    )
    is_active: models.BooleanField[bool] = models.BooleanField(
        default=True,
        help_text="Flag indicating if the case is currently active.",
        db_index=True,
    )
    is_closed: models.BooleanField[bool] = models.BooleanField(
        default=False,
        help_text="Flag indicating if the case has been closed.",
        db_index=True,
    )
    is_deleted: models.BooleanField[bool] = models.BooleanField(
        default=False,
        help_text="Flag indicating if the case is marked as deleted.",
        db_index=True,
    )
    created_at: models.DateTimeField[datetime] = models.DateTimeField(
        auto_now_add=True, help_text="The date and time the case was created."
    )
    updated_at: models.DateTimeField[datetime] = models.DateTimeField(
        auto_now=True, help_text="The date and time the case was last updated."
    )

    if TYPE_CHECKING:
        pass

    class Meta:
        ordering = ["-start_date", "patient"]
        verbose_name = "Case"
        verbose_name_plural = "Cases"

    def __str__(self) -> str:
        string_representation = f"Case {self.pk} ({self.patient})"
        if self.start_date:
            string_representation += f" - {self.start_date.strftime('%Y-%m-%d')}"
        if self.end_date:
            string_representation += f" - {self.end_date.strftime('%Y-%m-%d')}"
        return string_representation

    def close(self, end_date: CaseEndDate = None) -> None:
        """Close this case with a provided end date or the current time."""
        self.is_closed = True
        self.is_active = False
        if end_date:
            self.end_date = end_date
        elif not self.end_date:
            self.end_date = timezone.now()
        self.save()

    def reopen(self) -> None:
        """Reopen this case if it was closed."""
        self.is_closed = False
        self.is_active = True
        self.save()

    def mark_as_deleted(self) -> None:
        """Mark this case as deleted."""
        self.is_deleted = True
        self.is_active = False
        self.save()

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, cast, Any

from django.core.exceptions import ValidationError
from django.db import models
from lx_dtypes.models.contracts.subcategory_validation import (
    NumericalDescriptorContract,
    SubcategoryDictContract,
)

from endoreg_db.schemas import (
    validate_patient_numerical_descriptors,
    validate_patient_subcategories,
)

if TYPE_CHECKING:
    from endoreg_db.utils.links import ModelLinks


class PatientEvent(models.Model):
    """
    Represents a specific event occurrence for a patient, potentially with classification details.

    Links a patient to an event type, dates, description, and optional classification choices,
    subcategories, and numerical descriptors.
    """

    patient: models.ForeignKey[Any, Any] = models.ForeignKey(
        "Patient", on_delete=models.CASCADE, related_name="events"
    )
    event: models.ForeignKey[Any, Any] = models.ForeignKey(
        "Event", on_delete=models.CASCADE, related_name="patient_events"
    )
    date_start: models.DateField[Any, Any] = models.DateField()
    date_end: models.DateField[Any, Any] = models.DateField(blank=True, null=True)
    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)
    classification_choice: models.ForeignKey[Any, Any] = models.ForeignKey(
        "EventClassificationChoice",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )

    subcategories: models.JSONField[dict[str, SubcategoryDictContract]] = (
        models.JSONField(default=dict)
    )
    numerical_descriptors: models.JSONField[dict[str, NumericalDescriptorContract]] = (
        models.JSONField(default=dict)
    )

    last_update: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

    if TYPE_CHECKING:
        pass

    @property
    def links(self) -> ModelLinks:
        """
        Returns a dictionary of links related to this PatientEvent.
        Currently, it only includes the patient and event.
        """
        return ModelLinks(patient_events=[self], events=[self.event])

    @property
    def date(self) -> date:
        """
        Returns the start date of the event.
        """
        return self.date_start

    def __str__(self) -> str:
        """
        Returns a string representation of the event's start date and name.
        """
        return str(self.date_start) + ": " + self.event.name

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        for field_name, validator in (
            ("subcategories", validate_patient_subcategories),
            ("numerical_descriptors", validate_patient_numerical_descriptors),
        ):
            try:
                setattr(self, field_name, validator(getattr(self, field_name)))
            except ValueError as exc:
                errors[field_name] = str(exc)
        if errors:
            raise ValidationError(errors)

    def save(self, *args: object, **kwargs: object) -> None:
        self.clean()
        super().save(*args, **kwargs)

    def set_subcategories_from_classification_choice(
        self,
    ) -> dict[str, SubcategoryDictContract]:
        """Copies subcategory definitions from the linked classification choice."""
        if self.classification_choice:
            self.subcategories = cast(
                dict[str, SubcategoryDictContract],
                self.classification_choice.subcategories,
            )
            self.save()

        return self.subcategories

    def set_numerical_descriptors_from_classification_choice(
        self,
    ) -> dict[str, NumericalDescriptorContract]:
        """Copies numerical descriptor definitions from the linked classification choice."""
        if self.classification_choice:
            self.numerical_descriptors = cast(
                dict[str, NumericalDescriptorContract],
                self.classification_choice.numerical_descriptors,
            )
            self.save()

        return self.numerical_descriptors

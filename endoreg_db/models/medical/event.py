from __future__ import annotations
from typing import TYPE_CHECKING, Any, Unpack

from django.core.exceptions import ValidationError
from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager

from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from endoreg_db.schemas.classification_choice import (
    ClassificationChoiceJSONValidationError,
    validate_classification_choice_json_fields,
)

if TYPE_CHECKING:
    from endoreg_db.models import PatientEvent


class Event(models.Model):
    """
    A class representing an event.

    Attributes:
        name (str): The name of the event.
        description (str): A description of the event.
    """

    name: models.CharField[Any, Any] = models.CharField(max_length=100, unique=True)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)

    objects = NameNaturalKeyManager["Event"]()

    if TYPE_CHECKING:

        @property
        def patient_events(self) -> models.Manager["PatientEvent"]: ...

        @property
        def event_classifications(
            self,
        ) -> models.QuerySet["EventClassification"]: ...

    def natural_key(self) -> tuple[str]:
        """
        Returns a tuple representing the natural key for this instance.

        The natural key consists of the instance's unique name.
        """
        return (str(self.name),)

    def __str__(self) -> str:
        """
        Return a string representation of the instance's name.

        This method converts the 'name' attribute to a string, providing a human-readable
        representation of the model instance.
        """
        return str(self.name)


class EventClassification(models.Model):
    """
    Represents a classification system for events (e.g., TNM staging for cancer).
    """

    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)

    objects = NameNaturalKeyManager["EventClassification"]()

    event: models.ForeignKey[Any] = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="event_classifications",
    )

    if TYPE_CHECKING:

        @property
        def event_classification_choices(
            self,
        ) -> models.QuerySet["EventClassificationChoice"]: ...

    def natural_key(self) -> tuple[str]:
        """Returns the natural key (name) as a tuple."""
        return (str(self.name),)

    def __str__(self) -> str:
        """Returns the name of the classification."""
        return str(self.name)

    def get_choices(self) -> list["EventClassificationChoice"]:
        """Retrieves all choices associated with this classification."""
        return [choice for choice in self.event_classification_choices.all()]


class EventClassificationChoice(models.Model):
    """
    Represents a specific choice within an event classification system (e.g., T1, N0, M0).

    Can define associated subcategories and numerical descriptors.
    """

    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)
    subcategories: models.JSONField[Any, Any] = models.JSONField(default=dict)
    numerical_descriptors: models.JSONField[Any, Any] = models.JSONField(default=dict)

    event_classification: models.ForeignKey[Any] = models.ForeignKey(
        EventClassification,
        on_delete=models.CASCADE,
        related_name="event_classification_choices",
    )

    objects = NameNaturalKeyManager["EventClassificationChoice"]()

    def clean(self) -> None:
        super().clean()
        try:
            validate_classification_choice_json_fields(self)
        except ClassificationChoiceJSONValidationError as exc:
            raise ValidationError({exc.field_name: str(exc)}) from exc

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.clean()
        super().save(*args, **kwargs)

    @property
    def event(self) -> Event:
        """Returns the associated Event instance."""
        return self.event_classification.event

    def natural_key(self) -> tuple[str]:
        """Returns the natural key (name) as a tuple."""
        return (str(self.name),)

    def __str__(self) -> str:
        """Returns the name of the classification choice."""
        return str(self.name)

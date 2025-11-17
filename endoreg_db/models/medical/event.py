from typing import TYPE_CHECKING, List

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import PatientEvent


class EventManager(models.Manager):
    """
    Manager for Event with custom query methods.
    """

    def get_by_natural_key(self, name: str) -> "Event":
        """
        Retrieves an Event instance using its natural key.

        This method returns the event whose name matches the provided natural key.
        It is primarily used to support Django's natural key serialization during
        data import/export and deserialization processes.

        Args:
            name: The unique event name serving as the natural key.

        Returns:
            The Event object corresponding to the given name.
        """
        return self.get(name=name)


class Event(models.Model):
    """
    A class representing an event.

    Attributes:
        name (str): The name of the event.
        description (str): A description of the event.
    """

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    objects = EventManager()

    if TYPE_CHECKING:
        patient_events: models.ForeignKey["PatientEvent"]

        @property
        def event_classifications(self) -> models.QuerySet["EventClassification"]: ...

    def natural_key(self):
        """
        Returns a tuple representing the natural key for this instance.

        The natural key consists of the instance's unique name.
        """
        return (self.name,)

    def __str__(self):
        """
        Return a string representation of the instance's name.

        This method converts the 'name' attribute to a string, providing a human-readable
        representation of the model instance.
        """
        return str(self.name)


class EventClassificationManager(models.Manager):
    """Manager for EventClassification with natural key support."""

    def get_by_natural_key(self, name):
        """Retrieves an EventClassification instance by its natural key (name)."""
        return self.get(name=name)


class EventClassification(models.Model):
    """
    Represents a classification system for events (e.g., TNM staging for cancer).
    """

    name = models.CharField(max_length=255, unique=True)

    objects = EventClassificationManager()

    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="event_classifications",
    )

    if TYPE_CHECKING:
        event: models.ForeignKey["Event"]

        @property
        def event_classification_choices(self) -> models.QuerySet["EventClassificationChoice"]: ...

    def natural_key(self):
        """Returns the natural key (name) as a tuple."""
        return (self.name,)

    def __str__(self):
        """Returns the name of the classification."""
        return str(self.name)

    def get_choices(self) -> List["EventClassificationChoice"]:
        """Retrieves all choices associated with this classification."""
        choices: List[EventClassificationChoice] = [_ for _ in self.event_classification_choices.all()]
        return choices


class EventClassificationChoiceManager(models.Manager):
    """Manager for EventClassificationChoice with natural key support."""

    def get_by_natural_key(self, name):
        """Retrieves an EventClassificationChoice instance by its natural key (name)."""
        return self.get(name=name)


class EventClassificationChoice(models.Model):
    """
    Represents a specific choice within an event classification system (e.g., T1, N0, M0).

    Can define associated subcategories and numerical descriptors.
    """

    name = models.CharField(max_length=255, unique=True)
    subcategories = models.JSONField(default=dict)
    numerical_descriptors = models.JSONField(default=dict)

    event_classification = models.ForeignKey(
        EventClassification,
        on_delete=models.CASCADE,
        related_name="event_classification_choices",
    )

    objects = EventClassificationChoiceManager()

    @property
    def event(self):
        """Returns the associated Event instance."""
        return self.event_classification.event

    def natural_key(self):
        """Returns the natural key (name) as a tuple."""
        return (self.name,)

    def __str__(self):
        """Returns the name of the classification choice."""
        return str(self.name)

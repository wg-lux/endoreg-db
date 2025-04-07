from django.db import models
from typing import List


class EventManager(models.Manager):
    """
    Manager for Event with custom query methods.
    """

    def get_by_natural_key(self, name: str) -> "Event":
        """
        Retrieve an Event instance using its natural key.
        
        Args:
            name (str): The event's name serving as its natural key.
        
        Returns:
            Event: The event instance with the specified name.
        """
        return self.get(name=name)


class Event(models.Model):
    """
    A class representing an event.

    Attributes:
        name (str): The name of the event.
        name_de (str): The German name of the event.
        name_en (str): The English name of the event.
        description (str): A description of the event.
    """

    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    event_classification = models.ForeignKey(
        "EventClassification",
        on_delete=models.CASCADE,
        related_name="events",
        null=True,
        blank=True,
    )
    objects = EventManager()

    def natural_key(self):
        """
        Returns the natural key tuple for this instance.
        
        The tuple contains the instance's name attribute and is used for
        serialization and unique identification.
        """
        return (self.name,)

    def __str__(self):
        """Return the string representation of this object based on its name."""
        return str(self.name)


class EventClassificationManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieve an instance by its natural key.
        
        Uses the provided name to look up and return the corresponding instance.
        
        Args:
            name: The natural key value corresponding to the instance's name.
        
        Returns:
            The instance matching the given name.
        """
        return self.get(name=name)


class EventClassification(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    objects = EventClassificationManager()

    def natural_key(self):
        """
        Return the natural key for the instance.
        
        This method returns a tuple containing the instance's name, which uniquely identifies it.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the instance.
        
        This method returns the string value of the instance's 'name' attribute.
        """
        return str(self.name)

    def get_choices(self) -> List["EventClassificationChoice"]:
        """
        Return a list of event classification choice instances.
        
        Returns:
            List[EventClassificationChoice]: All choices linked to this classification via the 
            event_classification_choices relation.
        """
        choices: List[EventClassificationChoice] = [
            _ for _ in self.event_classification_choices.all()
        ]
        return choices


class EventClassificationChoiceManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieve an instance using its natural key.
        
        Args:
            name: The unique name identifying the instance.
        
        Returns:
            The instance with the matching name.
        """
        return self.get(name=name)


class EventClassificationChoice(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    subcategories = models.JSONField(default=dict)
    numerical_descriptors = models.JSONField(default=dict)

    event_classification = models.ForeignKey(
        EventClassification,
        on_delete=models.CASCADE,
        related_name="event_classification_choices",
    )

    objects = EventClassificationChoiceManager()

    def natural_key(self):
        """
        Return a tuple containing the instance's natural key.
        
        The tuple includes only the name attribute, which uniquely identifies the instance.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the instance.
        
        Converts the object's name attribute to a string for display purposes.
        """
        return str(self.name)

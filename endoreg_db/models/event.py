from django.db import models
from typing import List


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
        Return the natural key for this instance.
        
        The natural key is defined as a tuple containing the instance's unique name.
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
    def get_by_natural_key(self, name):
        """
        Retrieves an EventClassification instance using its natural key.
        
        This method returns the EventClassification whose "name" attribute matches the provided natural key.
        
        Args:
            name (str): The natural key representing the Event's name.
        
        Returns:
            EventClassification: The EventClassification instance with the specified name.
        """
        return self.get(name=name)


class EventClassification(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    objects = EventClassificationManager()

    def natural_key(self):
        """
        Return a tuple containing the natural key of the instance.
        
        The tuple consists solely of the object's name attribute, serving as its unique identifier for natural key lookups.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the object's name as a string.
        """
        return str(self.name)

    def get_choices(self) -> List["EventClassificationChoice"]:
        """
        Retrieves the event classification choices.
        
        Returns:
            List[EventClassificationChoice]: The list of choices associated with this classification.
        """
        choices: List[EventClassificationChoice] = [
            _ for _ in self.event_classification_choices.all()
        ]
        return choices


class EventClassificationChoiceManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a model instance using its natural key.
        
        Args:
            name: The value of the natural key corresponding to the instance's name field.
        
        Returns:
            The model instance with a name matching the provided natural key.
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
        Returns the natural key for the model instance.
        
        The natural key is defined as a tuple containing the instance's name, uniquely identifying it.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the instance's name.
        """
        return str(self.name)

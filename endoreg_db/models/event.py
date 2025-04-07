from django.db import models
from typing import List


class EventManager(models.Manager):
    """
    Manager for Event with custom query methods.
    """

    def get_by_natural_key(self, name: str) -> "Event":
        """
        Retrieve an Event instance by its natural key.
        
        Args:
            name (str): The event name used as the natural key.
        
        Returns:
            Event: The Event instance matching the provided name.
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
        Return a tuple containing the instance's unique name.
        
        This tuple serves as the natural key for the instance and can be used for
        serialization or object lookup where a unique identifier is required.
        """
        return (self.name,)

    def __str__(self):
        """
        Return a string representation of the instance.
        
        The method returns the string conversion of the instance's name attribute.
        """
        return str(self.name)


class EventClassificationManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieve a model instance using its natural key.
        
        This method returns the object whose 'name' field matches the provided natural key,
        typically used during serialization and deserialization.
        
        Args:
            name (str): The natural key used for lookup.
        
        Returns:
            The model instance that corresponds to the given natural key.
        """
        return self.get(name=name)


class EventClassification(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    objects = EventClassificationManager()

    def natural_key(self):
        """
        Return a tuple that serves as the natural key of the instance.
        
        The returned tuple contains the instance's name attribute, which uniquely identifies
        the object for serialization and natural key lookups.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the instance.
        
        This method returns the value of the instance's name attribute as a string,
        providing a human-readable representation of the object.
        """
        return str(self.name)

    def get_choices(self) -> List["EventClassificationChoice"]:
        """
        Returns the event classification choices.
        
        Retrieves and returns a list of all EventClassificationChoice instances associated with
        the current event classification via the reverse relationship.
            
        Returns:
            List[EventClassificationChoice]: A list of associated event classification choices.
        """
        choices: List[EventClassificationChoice] = [
            _ for _ in self.event_classification_choices.all()
        ]
        return choices


class EventClassificationChoiceManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieve a model instance using its natural key.
        
        Args:
            name: The unique name that identifies the model instance.
        
        Returns:
            The model instance whose 'name' field matches the provided natural key.
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
        
        The returned tuple includes the object's unique name attribute, which is used
        for natural key serialization.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the instance's name as a string.
        
        Converts the object's 'name' attribute to a string for consistent representation.
        """
        return str(self.name)

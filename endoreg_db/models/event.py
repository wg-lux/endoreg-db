from django.db import models

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

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
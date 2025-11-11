from django.db import models


class LabelTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class LabelType(models.Model):
    """
    A class representing a label type.

    Attributes:
        name (str): The name of the label type.
        description (str): A description of the label type.

    """

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)

    objects = LabelTypeManager()

    def natural_key(self):
        """Return the natural key of this label type"""
        return (self.name,)

    def __str__(self):
        return str(self.name)

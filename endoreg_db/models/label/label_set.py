from django.db import models
from typing import TYPE_CHECKING

class LabelSetManager(models.Manager):
    """
    Manager class for handling LabelSet model operations.
    Methods
    -------
    get_by_natural_key(name)

    """

    def get_by_natural_key(self, name):
        """Retrieves a LabelSet instance by its natural key (name)."""
        return self.get(name=name)


class LabelSet(models.Model):
    """
    A class representing a label set.

    Attributes:
        name (str): The name of the label set.
        description (str): A description of the label set.

    """

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    version = models.IntegerField()
    labels = models.ManyToManyField("Label", related_name="label_sets")

    objects = LabelSetManager()

    if TYPE_CHECKING:
        from .label import Label
        labels: models.QuerySet["Label"]

    def natural_key(self):
        """Return the natural key of this label set"""
        return (self.name,)

    def __str__(self) -> str:
        return str(self.name)

    def get_labels_in_order(self) -> list["Label"]:
        """
        Get all labels in this label set as list in the correct order.
        Ordered by string representation (a is first).
        """
        labels = list(self.labels.all())
        labels.sort(key=lambda x: x.name)
        return labels

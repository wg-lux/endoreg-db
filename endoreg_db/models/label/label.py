from django.db import models
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from .label_type import LabelType
    from .label_set import LabelSet
class LabelManager(models.Manager):
    """Manager class for handling Label model operations."""

    def get_by_natural_key(self, name):
        """Retrieves a Label instance by its natural key (name)."""
        return self.get(name=name)


class Label(models.Model):
    """
    A class representing a label.

    Attributes:
        name (str): The name of the label.
        label_type (LabelType): The type of the label.
        description (str): A description of the label.

    """

    name = models.CharField(max_length=255)
    label_type = models.ForeignKey(
        "LabelType", on_delete=models.CASCADE, related_name="labels"
    )
    description = models.TextField(blank=True, null=True)

    objects = LabelManager()

    if TYPE_CHECKING:
        label_type: "LabelType"
        label_sets: models.QuerySet["LabelSet"]

    def natural_key(self):
        """Return the natural key of this label"""
        return (self.name,)

    def __str__(self):
        return str(self.name)


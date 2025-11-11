from typing import TYPE_CHECKING

from django.db import models


class LabelSetManager(models.Manager):
    """
    Manager class for handling LabelSet model operations.
    Methods
    -------
    get_by_natural_key(name)

    """

    def get_by_natural_key(self, name, version=None):
        """Retrieves a LabelSet instance by its natural key (name[, version])."""

        queryset = self.filter(name=name)
        if version not in (None, "", -1):
            queryset = queryset.filter(version=version)

        labelset = queryset.order_by("-version").first()
        if not labelset:
            raise self.model.DoesNotExist(f"LabelSet with name='{name}' and version='{version}' not found")
        return labelset


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
        from typing import cast

        from .label import Label

        labels = cast(models.manager.RelatedManager["Label"], labels)

    def natural_key(self):
        """Return the natural key of this label set"""
        return (self.name, self.version)

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

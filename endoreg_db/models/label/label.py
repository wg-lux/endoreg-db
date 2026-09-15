from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias, cast, Any

from django.db import models

if TYPE_CHECKING:
    from .label_set import LabelSet
    from .label_type import LabelType

NoLabelValue: TypeAlias = None
NoLabelDescriptionValue: TypeAlias = None
LabelTypeRelation: TypeAlias = "LabelType | NoLabelValue"
LabelDescription: TypeAlias = "str | NoLabelDescriptionValue"


class LabelManager(models.Manager["Label"]):
    """Manager class for handling Label model operations."""

    def get_by_natural_key(self, name: str) -> "Label":
        """Retrieves a Label instance by its natural key (name)."""
        label = self.resolve_by_name(name)
        if label is None:
            raise Label.DoesNotExist("Label matching query does not exist.")
        return label

    def resolve_by_name(
        self, name: str, *, case_insensitive: bool = False
    ) -> "Label | NoLabelValue":
        """Return the deterministic first label for a natural name."""
        normalized_name = str(name).strip()
        lookup = (
            {"name__iexact": normalized_name}
            if case_insensitive
            else {"name": normalized_name}
        )
        return self.filter(**lookup).order_by("pk").first()


class Label(models.Model):
    """
    A class representing a label.

    Attributes:
        name (str): The name of the label.
        label_type (LabelType): The type of the label.
        description (str): A description of the label.

    """

    name: models.CharField[Any, Any] = models.CharField(max_length=255)
    label_type: models.ForeignKey[Any] = models.ForeignKey(
        "LabelType",
        on_delete=models.CASCADE,
        related_name="labels",
        blank=True,
        null=True,
    )
    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)

    objects = LabelManager()

    if TYPE_CHECKING:

        @property
        def label_sets(self) -> models.QuerySet["LabelSet"]: ...

    def natural_key(self) -> tuple[str]:
        """Return the natural key of this label"""
        return (self.name,)

    def __str__(self) -> str:
        return str(self.name)

    @classmethod
    def get_outside_label(cls) -> "Label":
        """
        Returns the label instance for 'outside'.
        """
        label = cast(LabelManager, cls.objects).resolve_by_name("outside")
        if label is None:
            raise ValueError("'outside' label does not exist in the database")
        return label

    @classmethod
    def get_low_quality_label(cls) -> "Label":
        """
        Retrieve the label instance with the name 'low_quality'.

        Raises:
            ValueError: If a label with the name 'low_quality' does not exist.
        """
        label = cast(LabelManager, cls.objects).resolve_by_name("low_quality")
        if label is None:
            raise ValueError("'low_quality' label does not exist in the database")
        return label

    @classmethod
    def get_or_create_from_name(cls, name: str) -> tuple["Label", bool]:
        """
        Retrieve or create a Label instance with the specified name.

        Parameters:
            name (str): The name of the label to retrieve or create.

        Returns:
            tuple: A tuple containing the Label instance and a boolean indicating whether the instance was created (True) or retrieved (False).
        """
        label = cast(LabelManager, cls.objects).resolve_by_name(name)
        if label is not None:
            return label, False
        label = cls.objects.create(name=str(name).strip())
        return label, True

from __future__ import annotations

"""Module for Organ models."""

from django.db import models


class OrganManager(models.Manager["Organ"]):
    """Manager for Organ model."""

    def get_by_natural_key(self, name: str) -> "Organ":
        """Retrieve an Organ by its natural key."""
        return self.get(name=name)

    def all_names(self) -> list[str]:
        """Return a list of all organ names."""
        return [str(name) for name in self.all().values_list("name", flat=True)]


class Organ(models.Model):
    """Model representing an organ."""

    name: models.CharField[str] = models.CharField(max_length=100, unique=True)
    description: models.TextField[str | None] = models.TextField(blank=True, null=True)

    # Deprecated
    # location_choices = models.ManyToManyField(
    #     'FindingClassificationChoice',
    #     blank=True, related_name='organs'
    # )

    objects = OrganManager()

    def natural_key(self) -> tuple[str]:
        """Return the natural key for the organ."""
        return (str(self.name),)

    def __str__(self) -> str:
        """Return string representation of the organ."""
        return str(self.name)

"""Module for Organ models."""

from django.db import models


class OrganManager(models.Manager):
    """Manager for Organ model."""

    def get_by_natural_key(self, name):
        """Retrieve an Organ by its natural key."""
        return self.get(name=name)

    def all_names(self):
        """Return a list of all organ names."""
        return list(self.all().values_list("name", flat=True))


class Organ(models.Model):
    """Model representing an organ."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    # Deprecated
    # location_choices = models.ManyToManyField(
    #     'FindingClassificationChoice',
    #     blank=True, related_name='organs'
    # )

    objects = OrganManager()

    def natural_key(self):
        """Return the natural key for the organ."""
        return (self.name,)

    def __str__(self):
        """Return string representation of the organ."""
        return str(self.name)

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from ..administration import Patient


class GenderManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class Gender(models.Model):
    """A class representing gender."""

    objects = GenderManager()

    name = models.CharField(max_length=255)
    abbreviation = models.CharField(max_length=255, null=True)
    description = models.TextField(blank=True, null=True)

    if TYPE_CHECKING:

        @property
        def patients(self) -> models.QuerySet["Patient"]: ...

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return str(self.name)

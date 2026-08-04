from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import (
        Qualification,
    )


class QualificationTypeManager(models.Manager):
    def get_queryset(self):
        """
        Returns a queryset of active qualification types.

        Only includes records where the `is_active` field is set to True.
        """
        return super().get_queryset().filter(is_active=True)


class QualificationType(models.Model):
    """
    Model representing a qualification type.
    """

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    objects = QualificationTypeManager()

    if TYPE_CHECKING:
        qualification: models.QuerySet["Qualification"]

    def __str__(self):
        """
        Returns the string representation of the qualification type's name.
        """
        return str(self.name)

from typing import TYPE_CHECKING, cast

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import (
        QualificationType,
    )


class QualificationManager(models.Manager):
    def get_queryset(self):
        """
        Returns a queryset of qualifications filtered to include only active entries.
        """
        return super().get_queryset().filter(is_active=True)


class Qualification(models.Model):
    """
    Model representing a qualification.
    """

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    qualification_types = models.ManyToManyField(
        "QualificationType",
        related_name="qualifications",
    )
    if TYPE_CHECKING:
        qualification_types = cast(models.manager.RelatedManager["QualificationType"], qualification_types)

    objects = QualificationManager()

    def __str__(self):
        """
        Returns the string representation of the qualification's name.
        """
        return str(self.name)

from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import (
        QualificationType,
    )

class QualificationManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)
    
class Qualification(models.Model):
    """
    Model representing a qualification.
    """
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)

    qualification_types = models.ManyToManyField(
        "QualificationType",
        related_name="qualifications",
    )

    if TYPE_CHECKING:
        qualification_type: models.QuerySet["QualificationType"]



    objects = QualificationManager()

    def __str__(self):
        return str(self.name)

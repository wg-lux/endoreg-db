from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import (
        Qualification,
    )

class QualificationTypeManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)
    
class QualificationType(models.Model):
    """
    Model representing a qualification type.
    """
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)

    objects = QualificationTypeManager()

    if TYPE_CHECKING:
        qualification: models.QuerySet["Qualification"]

    def __str__(self):
        return str(self.name)

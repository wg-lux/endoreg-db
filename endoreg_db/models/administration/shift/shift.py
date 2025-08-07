from django.db import models
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from endoreg_db.models import (
        ShiftType,
        Qualification,
        CenterShift,
    )

class ShiftManager(models.Manager):
    def get_queryset(self):
        """
        Returns a queryset of active shifts.
        
        Only includes shifts where the 'is_active' field is set to True.
        """
        return super().get_queryset().filter(is_active=True)
    
class Shift(models.Model):
    """
    Model representing a shift.
    """
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)  


    shift_types = models.ManyToManyField(
        "ShiftType",
        related_name="shifts",
    )

    required_qualifications = models.ManyToManyField(
        "Qualification",
        related_name="shifts",
    )

    if TYPE_CHECKING:
        shift_types: models.QuerySet["ShiftType"]
        required_qualifications: models.QuerySet["Qualification"]
        center_shifts: models.QuerySet["CenterShift"]

    objects = ShiftManager()

    def __str__(self):
        """
        Returns the string representation of the shift using its name.
        """
        return str(self.name)
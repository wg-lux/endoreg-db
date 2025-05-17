from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import (
        Center,
        Shift,
        ScheduledDays,
    )

class CenterShiftManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class CenterShift(models.Model):
    """
    Model representing a center shift.
    """
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255)
    name_en = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)

    center = models.ForeignKey(
        "Center",
        on_delete=models.CASCADE,
        related_name="center_shifts",
    )
    shift = models.ForeignKey(
        "Shift",
        on_delete=models.CASCADE,
        related_name="center_shifts",
    )

    start_time = models.TimeField()
    end_time = models.TimeField()
    scheduled_days: models.ManyToManyField = models.ManyToManyField( # Add type hint
        "ScheduledDays",
        related_name="center_shifts",
    )

    #TODO add validator; the value should be between 0 and 1
    estimated_presence_fraction = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default=0.0,
    )

    if TYPE_CHECKING:
        pass # Keep an empty TYPE_CHECKING block if no other type hints are needed.

    def __str__(self):
        return f"{self.center} - {self.shift}"
    
    def initialize_scheduled_days(self):
        # shift_types
        pass
    
    def get_scheduled_days(self, infer = True):
        """
        Returns the scheduled days for the center shift.
        """
        sd = self.scheduled_days.all()
        if not sd.exists():
            if not infer:
                raise ValueError("No scheduled days found for this center shift.")
            
        
        return sd
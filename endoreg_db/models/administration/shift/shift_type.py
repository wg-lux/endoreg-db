from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import Shift

# Those are defined in our base data
NAME_REGULAR = "regular"
NAME_ON_CALL = "on_call"
NAME_WARD = "ward"
NAME_OFF_HOURS = "off_hours"

class ShiftTypeManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)
    
class ShiftType(models.Model):
    """
    Model representing a Shift type.
    """
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)

    objects = ShiftTypeManager()

    if TYPE_CHECKING:
        shift: models.QuerySet["Shift"]

    def __str__(self):
        return str(self.name)

    @classmethod
    def get_type_regular(cls) -> "ShiftType":
        """
        Get the common shift type by name.
        """
        return cls.objects.get(name=NAME_REGULAR)
    
    @classmethod
    def get_type_on_call(cls) -> "ShiftType":
        """
        Get the on-call shift type by name.
        """
        return cls.objects.get(name=NAME_ON_CALL)
    
    @classmethod
    def get_type_ward(cls) -> "ShiftType":
        """
        Get the ward shift type by name.
        """
        return cls.objects.get(name=NAME_WARD)
    
    @classmethod
    def get_type_off_hours(cls) -> "ShiftType":
        """
        Get the off-hours shift type by name.
        """
        return cls.objects.get(name=NAME_OFF_HOURS)

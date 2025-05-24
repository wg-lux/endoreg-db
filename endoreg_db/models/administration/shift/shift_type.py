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
        """
        Returns a queryset of active shift types.
        
        Only shift types with `is_active=True` are included in the queryset.
        """
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
        """
        Returns the name of the shift type as its string representation.
        """
        return str(self.name)

    @classmethod
    def get_type_regular(cls) -> "ShiftType":
        """
        Retrieves the ShiftType instance representing a regular shift.
        
        Returns:
            The ShiftType object with the name corresponding to a regular shift.
        
        Raises:
            ShiftType.DoesNotExist: If no ShiftType with the regular name exists.
        """
        return cls.objects.get(name=NAME_REGULAR)
    
    @classmethod
    def get_type_on_call(cls) -> "ShiftType":
        """
        Retrieves the ShiftType instance representing the on-call shift.
        
        Returns:
            The ShiftType object with the predefined on-call name.
        
        Raises:
            ShiftType.DoesNotExist: If no on-call shift type exists.
            ShiftType.MultipleObjectsReturned: If multiple on-call shift types exist.
        """
        return cls.objects.get(name=NAME_ON_CALL)
    
    @classmethod
    def get_type_ward(cls) -> "ShiftType":
        """
        Retrieves the ShiftType instance representing a ward shift.
        
        Returns:
            The ShiftType object with the predefined name for ward shifts.
        
        Raises:
            ShiftType.DoesNotExist: If no ShiftType with the ward name exists.
        """
        return cls.objects.get(name=NAME_WARD)
    
    @classmethod
    def get_type_off_hours(cls) -> "ShiftType":
        """
        Retrieves the ShiftType instance representing the off-hours shift.
        
        Returns:
            The ShiftType object with the name corresponding to off-hours.
        """
        return cls.objects.get(name=NAME_OFF_HOURS)

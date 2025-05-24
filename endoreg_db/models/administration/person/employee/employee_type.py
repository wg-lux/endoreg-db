from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import (
        Qualification,
        Employee,
    )

class EmployeeTypeManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)
    
class EmployeeType(models.Model):
    """
    Model representing an employee type.
    """
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    qualifications = models.ManyToManyField(
        "Qualification",
        related_name="employee_types",
    )

    if TYPE_CHECKING:
        qualifications: models.QuerySet["Qualification"]
        employees: models.QuerySet["Employee"]

    objects = EmployeeTypeManager()

    def __str__(self):
        return str(self.name)

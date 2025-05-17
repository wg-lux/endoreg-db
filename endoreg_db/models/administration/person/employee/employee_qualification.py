from django.db import models

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from endoreg_db.models import (
        Employee,
        Qualification,
    )

class EmployeeQualification(models.Model):
    """
    Model representing an employee's qualification.
    """
    employee = models.OneToOneField(
        "Employee",
        on_delete=models.CASCADE,
        related_name="qualification",
    )
    qualifications = models.ManyToManyField(
        "Qualification",
        # on_delete=models.CASCADE,
        related_name="employee_qualifications",
    )

    if TYPE_CHECKING:
        employee: Employee
        qualification: models.QuerySet["Qualification"]

    def __str__(self):
        return f"{self.employee} - {self.qualification}"
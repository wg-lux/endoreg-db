from typing import TYPE_CHECKING

from django.db import models

from ..person import Person

if TYPE_CHECKING:
    from endoreg_db.models import (
        EmployeeQualification,
        EmployeeType,
    )


class Employee(Person):
    """
    Model representing an employee.
    """

    employee_type = models.ForeignKey(
        "EmployeeType",
        on_delete=models.CASCADE,
        related_name="employees",
    )

    if TYPE_CHECKING:
        employee_type: models.ForeignKey["EmployeeType"]
        # qualification is a OneToOneField defined in the EmployeeQualification model
        qualification: models.OneToOneField["EmployeeQualification"]

    def __str__(self):
        """
        Returns a string representation of the Employee, including the class name, full name if available, and employee type if set.
        """
        str_repr = self.__class__.__name__
        if self.first_name and self.last_name:
            str_repr += f" {self.first_name} {self.last_name}"

        if self.employee_type:
            str_repr += f" ({self.employee_type})"
        return str_repr

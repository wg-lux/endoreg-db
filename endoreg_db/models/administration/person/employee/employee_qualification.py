from typing import TYPE_CHECKING, cast

from django.db import models

if TYPE_CHECKING:
    from django.db.models.manager import RelatedManager

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
        related_name="employee_qualifications",
    )

    if TYPE_CHECKING:
        employee: models.ForeignKey["Employee"]
        qualifications = cast(RelatedManager[Qualification], qualifications)

    def __str__(self):
        """
        Returns a human-readable string summarizing the employee and their qualifications.

        If the employee has qualifications, lists them separated by commas; otherwise, indicates that no qualifications are assigned.
        """
        qualification_list = self.qualifications.all()
        if qualification_list:
            # Assuming Qualification model has a decent __str__ representation
            qual_names = ", ".join(str(q) for q in qualification_list)
            return f"{self.employee} - Qualifications: {qual_names}"
        return f"{self.employee} - No qualifications assigned"

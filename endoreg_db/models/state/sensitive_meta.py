from typing import TYPE_CHECKING

from django.db import models

from .abstract import AbstractState


class SensitiveMetaState(AbstractState):
    """State for sensitive meta data."""

    dob_verified = models.BooleanField(default=False)
    names_verified = models.BooleanField(default=False)

    origin = models.OneToOneField(
        "SensitiveMeta",
        on_delete=models.CASCADE,
        related_name="state",
        null=True,
        blank=True,
    )

    if TYPE_CHECKING:
        from endoreg_db.models import SensitiveMeta

        origin: models.OneToOneField["SensitiveMeta|None"]

    @property
    def is_verified(self) -> bool:
        """
        Return True if both date of birth and names have been verified; otherwise, return False.
        """
        return self.dob_verified and self.names_verified

    def mark_dob_verified(self):
        """
        Set the date of birth verification status to True and persist the change to the database.
        """
        self.dob_verified = True
        self.save(update_fields=["dob_verified"])

    def mark_names_verified(self):
        """
        Mark the names as verified and persist the change to the database.
        """
        self.names_verified = True
        self.save(update_fields=["names_verified"])

    class Meta:
        verbose_name = "Sensitive Meta State"
        verbose_name_plural = "Sensitive Meta States"

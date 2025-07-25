from django.db import models
from .abstract import AbstractState
from typing import TYPE_CHECKING

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

        origin: "SensitiveMeta"

    @property
    def is_verified(self) -> bool:
        """
        Check if the state is verified.

        Returns:
            bool: True if the state is verified, False otherwise.
        """
        return self.dob_verified and self.names_verified

    def mark_dob_verified(self):
        """Mark the date of birth as verified."""
        self.dob_verified = True
        self.save(update_fields=["dob_verified"])

    def mark_names_verified(self):
        """Mark the names as verified."""
        self.names_verified = True
        self.save(update_fields=["names_verified"])
    class Meta:
        verbose_name = "Sensitive Meta State"
        verbose_name_plural = "Sensitive Meta States"

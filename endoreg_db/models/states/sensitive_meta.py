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
    class Meta:
        verbose_name = "Sensitive Meta State"
        verbose_name_plural = "Sensitive Meta States"

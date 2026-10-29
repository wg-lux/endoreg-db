from __future__ import annotations
from typing import Any
from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.schemas.sensitive_meta_verification import (
        SensitiveMetaVerificationCommand,
    )

from .abstract import AbstractState


class SensitiveMetaState(AbstractState):
    """State for sensitive meta data."""

    origin_id: int | None

    dob_verified: models.BooleanField[bool, Any] = models.BooleanField(default=False)
    names_verified: models.BooleanField[bool, Any] = models.BooleanField(default=False)

    origin: models.OneToOneField["SensitiveMeta | None"] = models.OneToOneField(
        "SensitiveMeta",
        on_delete=models.CASCADE,
        related_name="state",
        null=True,
        blank=True,
    )

    if TYPE_CHECKING:
        from endoreg_db.models import SensitiveMeta

        pass

    @property
    def is_verified(self) -> bool:
        """
        Return True if both date of birth and names have been verified; otherwise, return False.
        """
        return self.dob_verified and self.names_verified

    def mark_dob_verified(self) -> None:
        from endoreg_db.schemas.sensitive_meta_verification import (
            SensitiveMetaVerificationCommand,
        )

        self._update_verification(SensitiveMetaVerificationCommand(dob_verified=True))

    def mark_names_verified(self) -> None:
        from endoreg_db.schemas.sensitive_meta_verification import (
            SensitiveMetaVerificationCommand,
        )

        self._update_verification(SensitiveMetaVerificationCommand(names_verified=True))

    def _update_verification(self, command: SensitiveMetaVerificationCommand) -> None:
        from endoreg_db.services.patient_identity.verification import (
            update_sensitive_meta_verification,
        )

        if self.origin_id is None or self.pk is None:
            raise ValueError("Verification requires a persisted SensitiveMeta state")
        result = update_sensitive_meta_verification(
            sensitive_meta_id=self.origin_id, command=command
        )
        self.dob_verified = result.dob_verified
        self.names_verified = result.names_verified

    class Meta(AbstractState.Meta):
        verbose_name = "Sensitive Meta State"
        verbose_name_plural = "Sensitive Meta States"

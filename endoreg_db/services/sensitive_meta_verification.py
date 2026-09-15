from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.state.sensitive_meta import SensitiveMetaState
from endoreg_db.schemas.sensitive_meta_verification import (
    SensitiveMetaVerificationCommand,
)


@dataclass(frozen=True, slots=True)
class SensitiveMetaVerificationResult:
    dob_verified: bool
    names_verified: bool
    is_verified: bool


@transaction.atomic
def update_sensitive_meta_verification(
    *,
    sensitive_meta_id: int,
    command: SensitiveMetaVerificationCommand,
) -> SensitiveMetaVerificationResult:
    """Apply one partial verification update while serializing concurrent writers."""

    sensitive_meta = SensitiveMeta.objects.select_for_update().get(pk=sensitive_meta_id)
    state, _ = SensitiveMetaState.objects.select_for_update().get_or_create(
        origin=sensitive_meta
    )

    if command.dob_verified is not None:
        state.dob_verified = command.dob_verified
    if command.names_verified is not None:
        state.names_verified = command.names_verified

    state.save()
    return SensitiveMetaVerificationResult(
        dob_verified=state.dob_verified,
        names_verified=state.names_verified,
        is_verified=state.is_verified,
    )


__all__ = [
    "SensitiveMetaVerificationResult",
    "update_sensitive_meta_verification",
]

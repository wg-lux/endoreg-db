from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Generator
from contextlib import contextmanager

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
def get_or_create_sensitive_meta_state(sensitive_meta_id: int) -> SensitiveMetaState:
    """Create verification state under the same parent lock as verification writes."""
    sensitive_meta = SensitiveMeta.objects.select_for_update().get(pk=sensitive_meta_id)
    state, _ = SensitiveMetaState.objects.select_for_update().get_or_create(
        origin=sensitive_meta
    )
    return state


@transaction.atomic
def update_sensitive_meta_verification(
    *,
    sensitive_meta_id: int,
    command: SensitiveMetaVerificationCommand,
) -> SensitiveMetaVerificationResult:
    """Apply one partial verification update while serializing concurrent writers."""

    state = get_or_create_sensitive_meta_state(sensitive_meta_id)

    if command.dob_verified is not None:
        state.dob_verified = command.dob_verified
    if command.names_verified is not None:
        state.names_verified = command.names_verified

    state.save(update_fields=["dob_verified", "names_verified", "updated_at"])
    return SensitiveMetaVerificationResult(
        dob_verified=state.dob_verified,
        names_verified=state.names_verified,
        is_verified=state.is_verified,
    )


@contextmanager
def sensitive_meta_verification_guard(
    sensitive_meta: SensitiveMeta,
) -> Generator[None]:
    """Serialize saves with verification and revoke checks of changed persisted data."""
    with transaction.atomic():
        previous = (
            SensitiveMeta.objects.select_for_update()
            .filter(pk=sensitive_meta.pk)
            .first()
            if sensitive_meta.pk
            else None
        )
        yield
        if previous is None:
            return
        current = SensitiveMeta.objects.get(pk=sensitive_meta.pk)
        if current.direct_identifiers_cleared_at is not None:
            from endoreg_db.services.privacy.quality_evaluation import (
                require_erased_sensitive_meta,
            )

            require_erased_sensitive_meta(current)
            return
        names_changed = (previous.patient_first_name, previous.patient_last_name) != (
            current.patient_first_name,
            current.patient_last_name,
        )
        dob_changed = previous.patient_dob != current.patient_dob
        if names_changed or dob_changed:
            update_sensitive_meta_verification(
                sensitive_meta_id=current.pk,
                command=SensitiveMetaVerificationCommand(
                    names_verified=False if names_changed else None,
                    dob_verified=False if dob_changed else None,
                ),
            )
            sensitive_meta.state_safe.refresh_from_db()


__all__ = [
    "SensitiveMetaVerificationResult",
    "get_or_create_sensitive_meta_state",
    "sensitive_meta_verification_guard",
    "update_sensitive_meta_verification",
]

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.other.gender import Gender
from endoreg_db.models.state.sensitive_meta import SensitiveMetaState
from endoreg_db.schemas.sensitive_meta_update import SensitiveMetaUpdateCommand


class SensitiveMetaUpdateReferenceError(ValueError):
    """Base error for a named relation that cannot be resolved."""


class SensitiveMetaUpdateCenterNotFoundError(SensitiveMetaUpdateReferenceError):
    def __init__(self, center_name: str) -> None:
        self.center_name = center_name
        super().__init__(f"Center '{center_name}' does not exist.")


class SensitiveMetaUpdateGenderNotFoundError(SensitiveMetaUpdateReferenceError):
    def __init__(self, gender_name: str) -> None:
        self.gender_name = gender_name
        super().__init__(f"Gender '{gender_name}' does not exist.")


@dataclass(frozen=True, slots=True)
class SensitiveMetaUpdateResult:
    sensitive_meta: SensitiveMeta


def _resolve_center(command: SensitiveMetaUpdateCommand) -> Center | None:
    if "center_name" not in command.model_fields_set or command.center_name is None:
        return None
    center = Center.objects.filter(name=command.center_name).first()
    if center is None:
        raise SensitiveMetaUpdateCenterNotFoundError(command.center_name)
    return center


def _resolve_gender(command: SensitiveMetaUpdateCommand) -> Gender | None:
    if (
        "patient_gender_name" not in command.model_fields_set
        or command.patient_gender_name is None
    ):
        return None
    gender = Gender.objects.filter(name=command.patient_gender_name).first()
    if gender is None:
        raise SensitiveMetaUpdateGenderNotFoundError(command.patient_gender_name)
    return gender


@transaction.atomic
def update_sensitive_meta(
    *,
    sensitive_meta_id: int,
    command: SensitiveMetaUpdateCommand,
) -> SensitiveMetaUpdateResult:
    """Apply one partial update while serializing concurrent writers."""

    sensitive_meta = SensitiveMeta.objects.select_for_update().get(pk=sensitive_meta_id)
    center = _resolve_center(command)
    gender = _resolve_gender(command)

    if center is not None:
        sensitive_meta.center = center
    if gender is not None:
        sensitive_meta.patient_gender = gender

    regular_update_data = command.regular_update_data()
    if regular_update_data:
        sensitive_meta.update_from_dict(regular_update_data)
    elif center is not None or gender is not None:
        sensitive_meta.save()

    verification_fields = command.model_fields_set & {
        "dob_verified",
        "names_verified",
    }
    if verification_fields:
        state, _ = SensitiveMetaState.objects.select_for_update().get_or_create(
            origin=sensitive_meta
        )
        if "dob_verified" in verification_fields:
            state.dob_verified = bool(command.dob_verified)
        if "names_verified" in verification_fields:
            state.names_verified = bool(command.names_verified)
        state.save()

    return SensitiveMetaUpdateResult(sensitive_meta=sensitive_meta)


__all__ = [
    "SensitiveMetaUpdateCenterNotFoundError",
    "SensitiveMetaUpdateGenderNotFoundError",
    "SensitiveMetaUpdateReferenceError",
    "SensitiveMetaUpdateResult",
    "update_sensitive_meta",
]

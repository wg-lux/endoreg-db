from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, cast

from django.db import models, transaction
from django.utils import timezone

from endoreg_db.models.administration.case.case import Case

CASE_RELATION_FIELDS = (
    "patient_examinations",
    "patient_medications",
    "patient_medication_schedules",
    "patient_lab_samples",
    "patient_lab_values",
)


class _SettableCaseRelation(Protocol):
    def set(self, values: Sequence[models.Model]) -> None: ...


class CaseLifecycleError(ValueError):
    """Raised when a requested case lifecycle transition violates an invariant."""


def validate_case_relationships(
    *, patient_id: int, relationships: Mapping[str, Sequence[models.Model]]
) -> None:
    """Reject attachments that do not belong to the case patient."""
    for field_name, related_objects in relationships.items():
        for related_object in related_objects:
            related_patient_id = getattr(related_object, "patient_id", None)
            if related_patient_id != patient_id:
                raise ValueError(
                    f"{field_name} contains an object belonging to another patient"
                )


@transaction.atomic
def persist_case_graph(
    *,
    instance: Case | None,
    scalar_values: Mapping[str, object],
    relationships: Mapping[str, Sequence[models.Model]],
) -> Case:
    """Atomically create or update a case and its explicit clinical anchors."""
    patient = scalar_values.get("patient")
    patient_id = getattr(patient, "pk", None)
    if not isinstance(patient_id, int):
        raise ValueError("A persisted patient is required")
    validate_case_relationships(
        patient_id=patient_id,
        relationships=relationships,
    )

    if instance is None:
        case = Case(**cast(dict[str, Any], dict(scalar_values)))
        case.full_clean(exclude=CASE_RELATION_FIELDS)
        case.save()
    else:
        if instance.pk is None:
            raise ValueError("A persisted case is required for updates")
        case = Case.objects.select_for_update().get(pk=instance.pk)
        for field_name, value in scalar_values.items():
            setattr(case, field_name, value)
        case.full_clean(exclude=CASE_RELATION_FIELDS)
        case.save()

    for field_name, values in relationships.items():
        relation = cast(_SettableCaseRelation, getattr(case, field_name))
        relation.set(values)
    return case


@transaction.atomic
def close_case(*, instance: Case, end_date: datetime | None = None) -> Case:
    """Close a case under a row lock and make identical retries idempotent."""
    case = Case.objects.select_for_update().get(pk=instance.pk)
    target_end_date = end_date or case.end_date or timezone.now()
    if target_end_date < case.start_date:
        raise CaseLifecycleError("Case end date must not be earlier than start date.")
    if case.is_closed:
        if end_date is not None and case.end_date != end_date:
            raise CaseLifecycleError(
                "A closed case cannot be assigned a different end date."
            )
        return case

    case.end_date = target_end_date
    case.is_closed = True
    case.is_active = False
    case.full_clean()
    case.save(update_fields=("end_date", "is_closed", "is_active", "updated_at"))
    return case


@transaction.atomic
def reopen_case(*, instance: Case) -> Case:
    """Reopen a case under a row lock and make repeated requests idempotent."""
    case = Case.objects.select_for_update().get(pk=instance.pk)
    if case.is_active and not case.is_closed:
        return case

    case.is_closed = False
    case.is_active = True
    case.full_clean()
    case.save(update_fields=("is_closed", "is_active", "updated_at"))
    return case

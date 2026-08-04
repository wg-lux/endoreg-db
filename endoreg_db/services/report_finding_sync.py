from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, cast

from django.contrib.auth.models import User as AuthUser
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.finding.finding_classification import (
    FindingClassification,
    FindingClassificationChoice,
)
from endoreg_db.models.medical.finding.finding_intervention import FindingIntervention
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.models.medical.patient.patient_finding_classification import (
    PatientFindingClassification,
)
from endoreg_db.models.medical.patient.patient_finding_intervention import (
    PatientFindingIntervention,
)
from lx_dtypes.models.contracts.patient_examination_report import (
    PatientFindingClassificationSyncData,
    PatientFindingInterventionSyncData,
)
from lx_dtypes.models.contracts.patient_finding_classification_runtime import (
    PatientFindingClassificationNumericalDescriptorsData,
    PatientFindingClassificationNumericalDescriptorsPayload,
    PatientFindingClassificationSubcategoriesData,
    PatientFindingClassificationSubcategoriesPayload,
)


class _IdentifiedLike(Protocol):
    id: int


class _PatientFindingClassificationLike(Protocol):
    classification_id: int
    classification_choice_id: int
    is_active: bool
    subcategories: PatientFindingClassificationSubcategoriesData | None
    numerical_descriptors: PatientFindingClassificationNumericalDescriptorsData | None

    def save(self, *args: object, **kwargs: object) -> None: ...


class _PatientFindingInterventionLike(Protocol):
    intervention_id: int
    state: object
    date: date | None
    time_start: datetime | None
    time_end: datetime | None
    is_active: bool

    def save(self, *args: object, **kwargs: object) -> None: ...


class _PatientFindingLike(Protocol):
    finding_id: int
    updated_by_id: int | None
    is_active: bool
    deactivated_at: datetime | None
    deactivated_by: AuthUser | None
    updated_by: AuthUser | None

    def save(self, *args: object, **kwargs: object) -> None: ...


class _PatientFindingClassificationManager(Protocol):
    def filter(self, **kwargs: object) -> Sequence[PatientFindingClassification]: ...


class _PatientFindingInterventionManager(Protocol):
    def filter(self, **kwargs: object) -> Sequence[PatientFindingIntervention]: ...


class _PatientFindingReverseRelations(Protocol):
    classifications: _PatientFindingClassificationManager
    interventions: _PatientFindingInterventionManager


@dataclass(frozen=True, slots=True)
class _ResolvedClassificationSync:
    classification: FindingClassification
    choice: FindingClassificationChoice
    subcategories: PatientFindingClassificationSubcategoriesData | None
    numerical_descriptors: PatientFindingClassificationNumericalDescriptorsData | None


@dataclass(frozen=True, slots=True)
class _ResolvedInterventionSync:
    intervention: FindingIntervention
    state: object
    date: date | None
    time_start: datetime | None
    time_end: datetime | None


@dataclass(frozen=True, slots=True)
class _ResolvedFindingSync:
    finding: Finding
    classifications: Sequence[PatientFindingClassificationSyncData]
    interventions: Sequence[PatientFindingInterventionSyncData]


def parse_report_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValidationError({"date": "Invalid date format; expected YYYY-MM-DD."})


def _parse_report_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    raise ValidationError(
        {"datetime": "Invalid datetime format; expected ISO-8601 datetime."}
    )


def _resolve_finding(value: object) -> Finding | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return Finding.objects.filter(pk=value).first()
    if isinstance(value, str):
        return Finding.objects.filter(name=value).first()
    return None


def _resolve_finding_classification(value: object) -> FindingClassification | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return FindingClassification.objects.filter(pk=value).first()
    if isinstance(value, str):
        return FindingClassification.objects.filter(name=value).first()
    return None


def _resolve_finding_classification_choice(
    value: object,
) -> FindingClassificationChoice | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return FindingClassificationChoice.objects.filter(pk=value).first()
    if isinstance(value, str):
        return FindingClassificationChoice.objects.filter(name=value).first()
    return None


def _resolve_finding_intervention(value: object) -> FindingIntervention | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return FindingIntervention.objects.filter(pk=value).first()
    if isinstance(value, str):
        return FindingIntervention.objects.filter(name=value).first()
    return None


def _normalize_classification_subcategories(
    value: object | None,
) -> PatientFindingClassificationSubcategoriesData | None:
    if value is None:
        return None
    return cast(
        PatientFindingClassificationSubcategoriesData,
        PatientFindingClassificationSubcategoriesPayload.model_validate(
            value
        ).model_dump(mode="python"),
    )


def _normalize_classification_numerical_descriptors(
    value: object | None,
) -> PatientFindingClassificationNumericalDescriptorsData | None:
    if value is None:
        return None
    return cast(
        PatientFindingClassificationNumericalDescriptorsData,
        PatientFindingClassificationNumericalDescriptorsPayload.model_validate(
            value
        ).model_dump(mode="python"),
    )


def _resolve_classification_sync(
    item: PatientFindingClassificationSyncData,
) -> _ResolvedClassificationSync:
    classification = _resolve_finding_classification(
        item.get("classification_id", item.get("classification"))
    )
    choice = _resolve_finding_classification_choice(
        item.get("classification_choice_id", item.get("classification_choice"))
    )
    if classification is None or choice is None:
        raise ValidationError(
            {"classifications": "Unknown classification or classification choice."}
        )
    return _ResolvedClassificationSync(
        classification=classification,
        choice=choice,
        subcategories=_normalize_classification_subcategories(
            item.get("subcategories") if "subcategories" in item else None
        ),
        numerical_descriptors=_normalize_classification_numerical_descriptors(
            item.get("numerical_descriptors")
            if "numerical_descriptors" in item
            else None
        ),
    )


def _classification_identity(
    row: _PatientFindingClassificationLike,
) -> tuple[int, int]:
    return row.classification_id, row.classification_choice_id


def _create_classification(
    patient_finding: PatientFinding,
    resolved: _ResolvedClassificationSync,
) -> PatientFindingClassification:
    create_kwargs: dict[str, object] = {
        "finding": patient_finding,
        "classification": resolved.classification,
        "classification_choice": resolved.choice,
    }
    if resolved.subcategories is not None:
        create_kwargs["subcategories"] = resolved.subcategories
    if resolved.numerical_descriptors is not None:
        create_kwargs["numerical_descriptors"] = resolved.numerical_descriptors
    return PatientFindingClassification.objects.create(**create_kwargs)


def _update_classification_subcategories(
    match: _PatientFindingClassificationLike,
    resolved: _ResolvedClassificationSync,
) -> bool:
    subcategories = resolved.subcategories
    if subcategories is None or match.subcategories == subcategories:
        return False
    match.subcategories = subcategories
    return True


def _update_classification_numerical_descriptors(
    match: _PatientFindingClassificationLike,
    resolved: _ResolvedClassificationSync,
) -> bool:
    numerical_descriptors = resolved.numerical_descriptors
    if (
        numerical_descriptors is None
        or match.numerical_descriptors == numerical_descriptors
    ):
        return False
    match.numerical_descriptors = numerical_descriptors
    return True


def _update_classification(
    match: _PatientFindingClassificationLike,
    resolved: _ResolvedClassificationSync,
) -> None:
    changed = _update_classification_subcategories(match, resolved)
    changed |= _update_classification_numerical_descriptors(match, resolved)
    if not match.is_active:
        match.is_active = True
        changed = True
    if changed:
        match.save()


def _deactivate_unmatched_classifications(
    existing_active: Sequence[PatientFindingClassification],
    matched_ids: set[int],
) -> None:
    for row in existing_active:
        row_ref = cast(_PatientFindingClassificationLike, row)
        if cast(_IdentifiedLike, row).id not in matched_ids and row_ref.is_active:
            row_ref.is_active = False
            row_ref.save(update_fields=["is_active"])


def _sync_patient_finding_classifications(
    patient_finding: PatientFinding,
    payload: Sequence[PatientFindingClassificationSyncData],
) -> None:
    relations = cast(_PatientFindingReverseRelations, patient_finding)
    existing_active = list(relations.classifications.filter(is_active=True))
    existing_by_identity = {
        _classification_identity(cast(_PatientFindingClassificationLike, row)): cast(
            _PatientFindingClassificationLike, row
        )
        for row in existing_active
    }
    matched_ids: set[int] = set()
    for item in payload:
        resolved = _resolve_classification_sync(item)
        identity = (
            cast(_IdentifiedLike, resolved.classification).id,
            cast(_IdentifiedLike, resolved.choice).id,
        )
        match = existing_by_identity.get(identity)
        if match is None:
            match = cast(
                _PatientFindingClassificationLike,
                _create_classification(patient_finding, resolved),
            )
        else:
            _update_classification(match, resolved)
        matched_ids.add(cast(_IdentifiedLike, match).id)
    _deactivate_unmatched_classifications(existing_active, matched_ids)


def _resolve_intervention_sync(
    item: PatientFindingInterventionSyncData,
) -> _ResolvedInterventionSync:
    intervention = _resolve_finding_intervention(
        item.get("intervention_id", item.get("intervention"))
    )
    if intervention is None:
        raise ValidationError({"interventions": "Unknown intervention."})
    return _ResolvedInterventionSync(
        intervention=intervention,
        state=item.get("state"),
        date=parse_report_date(item.get("date")) if "date" in item else None,
        time_start=(
            _parse_report_datetime(item.get("time_start"))
            if "time_start" in item
            else None
        ),
        time_end=(
            _parse_report_datetime(item.get("time_end")) if "time_end" in item else None
        ),
    )


def _find_active_intervention(
    existing_active: Sequence[PatientFindingIntervention],
    resolved: _ResolvedInterventionSync,
) -> _PatientFindingInterventionLike | None:
    intervention_id = cast(_IdentifiedLike, resolved.intervention).id
    return next(
        (
            cast(_PatientFindingInterventionLike, row)
            for row in existing_active
            if cast(_PatientFindingInterventionLike, row).intervention_id
            == intervention_id
            and cast(_PatientFindingInterventionLike, row).state == resolved.state
        ),
        None,
    )


def _create_intervention(
    patient_finding: PatientFinding,
    resolved: _ResolvedInterventionSync,
) -> PatientFindingIntervention:
    return PatientFindingIntervention.objects.create(
        finding=patient_finding,
        intervention=resolved.intervention,
        state=resolved.state,
        date=resolved.date,
        time_start=resolved.time_start,
        time_end=resolved.time_end,
        is_active=True,
    )


def _update_intervention(
    match: _PatientFindingInterventionLike,
    resolved: _ResolvedInterventionSync,
) -> None:
    changed = False
    if match.date != resolved.date:
        match.date = resolved.date
        changed = True
    if match.time_start != resolved.time_start:
        match.time_start = resolved.time_start
        changed = True
    if match.time_end != resolved.time_end:
        match.time_end = resolved.time_end
        changed = True
    if not match.is_active:
        match.is_active = True
        changed = True
    if changed:
        match.save()


def _deactivate_unmatched_interventions(
    existing_active: Sequence[PatientFindingIntervention],
    matched_ids: set[int],
) -> None:
    for row in existing_active:
        row_ref = cast(_PatientFindingInterventionLike, row)
        if cast(_IdentifiedLike, row).id not in matched_ids and row_ref.is_active:
            row_ref.is_active = False
            row_ref.save(update_fields=["is_active"])


def _sync_patient_finding_interventions(
    patient_finding: PatientFinding,
    payload: Sequence[PatientFindingInterventionSyncData],
) -> None:
    relations = cast(_PatientFindingReverseRelations, patient_finding)
    existing_active = list(relations.interventions.filter(is_active=True))
    matched_ids: set[int] = set()
    for item in payload:
        resolved = _resolve_intervention_sync(item)
        match = _find_active_intervention(existing_active, resolved)
        if match is None:
            match = cast(
                _PatientFindingInterventionLike,
                _create_intervention(patient_finding, resolved),
            )
        else:
            _update_intervention(match, resolved)
        matched_ids.add(cast(_IdentifiedLike, match).id)
    _deactivate_unmatched_interventions(existing_active, matched_ids)


def _resolve_finding_sync(item: Mapping[str, object]) -> _ResolvedFindingSync:
    finding = _resolve_finding(item.get("finding_id", item.get("finding")))
    if finding is None:
        raise ValidationError({"findings": "Unknown finding."})
    return _ResolvedFindingSync(
        finding=finding,
        classifications=cast(
            Sequence[PatientFindingClassificationSyncData],
            item.get("classifications", []),
        ),
        interventions=cast(
            Sequence[PatientFindingInterventionSyncData],
            item.get("interventions", []),
        ),
    )


def _find_active_patient_finding(
    existing_active: Sequence[PatientFinding],
    finding: Finding,
) -> _PatientFindingLike | None:
    finding_id = cast(_IdentifiedLike, finding).id
    return next(
        (
            cast(_PatientFindingLike, row)
            for row in existing_active
            if cast(_PatientFindingLike, row).finding_id == finding_id
        ),
        None,
    )


def _create_patient_finding(
    patient_examination: PatientExamination,
    resolved: _ResolvedFindingSync,
    *,
    user: AuthUser | None,
) -> PatientFinding:
    patient_finding = PatientFinding(
        patient_examination=patient_examination,
        finding=resolved.finding,
        created_by=user,
        updated_by=user,
        is_active=True,
    )
    patient_finding.save()
    return patient_finding


def _update_patient_finding(
    match: _PatientFindingLike,
    *,
    user: AuthUser | None,
) -> None:
    changed_fields: list[str] = []
    if not match.is_active:
        match.is_active = True
        changed_fields.append("is_active")
    user_id = cast(_IdentifiedLike, user).id if user is not None else None
    if match.updated_by_id != user_id:
        match.updated_by = user
        changed_fields.append("updated_by")
    if changed_fields:
        match.save(update_fields=changed_fields)


def _sync_finding_children(
    patient_finding: PatientFinding,
    resolved: _ResolvedFindingSync,
) -> None:
    _sync_patient_finding_classifications(
        patient_finding,
        resolved.classifications,
    )
    _sync_patient_finding_interventions(
        patient_finding,
        resolved.interventions,
    )


def _deactivate_unmatched_findings(
    existing_active: Sequence[PatientFinding],
    matched_ids: set[int],
    *,
    user: AuthUser | None,
) -> None:
    for row in existing_active:
        if cast(_IdentifiedLike, row).id in matched_ids:
            continue
        row_ref = cast(_PatientFindingLike, row)
        row_ref.is_active = False
        row_ref.deactivated_at = timezone.now()
        row_ref.deactivated_by = user
        row_ref.updated_by = user
        row_ref.save(
            update_fields=[
                "is_active",
                "deactivated_at",
                "deactivated_by",
                "updated_by",
            ]
        )


def sync_report_findings(
    patient_examination: PatientExamination,
    findings_payload: Sequence[Mapping[str, object]],
    *,
    user: AuthUser | None,
) -> None:
    existing_active = list(
        patient_examination.patient_findings.filter(is_active=True).select_related(
            "finding"
        )
    )
    matched_ids: set[int] = set()
    for item in findings_payload:
        resolved = _resolve_finding_sync(item)
        match = _find_active_patient_finding(existing_active, resolved.finding)
        if match is None:
            patient_finding = _create_patient_finding(
                patient_examination,
                resolved,
                user=user,
            )
        else:
            _update_patient_finding(match, user=user)
            patient_finding = cast(PatientFinding, match)
        matched_ids.add(cast(_IdentifiedLike, patient_finding).id)
        _sync_finding_children(patient_finding, resolved)
    _deactivate_unmatched_findings(existing_active, matched_ids, user=user)


__all__ = ["parse_report_date", "sync_report_findings"]

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time
from typing import Protocol, cast

from django.db.models import Prefetch, Q

from endoreg_db.helpers.model_ids import model_pk
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.models.medical.patient.patient_finding_classification import (
    PatientFindingClassification,
)
from endoreg_db.models.medical.patient.patient_finding_intervention import (
    PatientFindingIntervention,
)
from lx_dtypes.models.contracts.patient_examination_report import (
    PatientExaminationHistoryContextData,
    PatientFindingClassificationHistoryData,
    PatientFindingHistoryData,
    PatientFindingInterventionHistoryData,
    PreviousPatientExaminationHistoryData,
    ReportJsonObject,
    report_json_safe,
)


class _IdentifiedLike(Protocol):
    id: int


class _HistoryRelatedManager(Protocol):
    def all(self) -> Iterable[object]: ...


class _PatientFindingSummaryLike(Protocol):
    pk: int
    patient_examination_id: int
    finding_id: int | None
    finding: object
    classifications: _HistoryRelatedManager
    interventions: _HistoryRelatedManager


class _PatientFindingClassificationSummaryLike(Protocol):
    pk: int
    classification_id: int | None
    classification_choice_id: int | None
    classification: object
    classification_choice: object
    subcategories: object
    numerical_descriptors: object


class _PatientFindingInterventionSummaryLike(Protocol):
    pk: int
    intervention_id: int | None
    intervention: object
    state: object
    date: object
    time_start: object
    time_end: object


class _PatientExaminationHistoryLike(Protocol):
    id: int
    examination_id: int | None
    examination: object
    date_start: object
    date_end: object


def _related_name(value: object) -> str | None:
    if value is None:
        return None
    name = getattr(value, "name", None)
    if name is None:
        return None
    if not isinstance(name, str):
        raise ValueError("Related history name must be a string.")
    return name


def _report_json_object(value: object) -> ReportJsonObject:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("Report history JSON fields must contain an object.")
    normalized = report_json_safe(cast(Mapping[str, object], value))
    if not isinstance(normalized, dict):
        raise ValueError("Report history JSON fields must normalize to an object.")
    return normalized


def _temporal_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    raise ValueError("Report history date and time values must be temporal values.")


def _serialize_patient_finding_classification(
    row: object,
) -> PatientFindingClassificationHistoryData:
    pfc = cast(_PatientFindingClassificationSummaryLike, row)
    return {
        "id": model_pk(row),
        "classification_id": pfc.classification_id,
        "classification_choice_id": pfc.classification_choice_id,
        "classification_name": _related_name(pfc.classification),
        "classification_choice_name": _related_name(pfc.classification_choice),
        "subcategories": _report_json_object(pfc.subcategories),
        "numerical_descriptors": _report_json_object(pfc.numerical_descriptors),
    }


def _serialize_patient_finding_intervention(
    row: object,
) -> PatientFindingInterventionHistoryData:
    pfi = cast(_PatientFindingInterventionSummaryLike, row)
    return {
        "id": model_pk(row),
        "intervention_id": pfi.intervention_id,
        "intervention_name": _related_name(pfi.intervention),
        "state": _temporal_value(pfi.state),
        "date": _temporal_value(pfi.date),
        "time_start": _temporal_value(pfi.time_start),
        "time_end": _temporal_value(pfi.time_end),
    }


def _serialize_patient_finding_summary(
    patient_finding: PatientFinding,
) -> PatientFindingHistoryData:
    patient_finding_ref = cast(_PatientFindingSummaryLike, patient_finding)

    classifications = [
        _serialize_patient_finding_classification(row)
        for row in patient_finding_ref.classifications.all()
    ]
    interventions = [
        _serialize_patient_finding_intervention(row)
        for row in patient_finding_ref.interventions.all()
    ]

    return {
        "patient_finding_id": model_pk(patient_finding),
        "instance_id": str(patient_finding.instance_id),
        "finding_id": patient_finding_ref.finding_id,
        "finding_name": _related_name(patient_finding_ref.finding),
        "classifications": classifications,
        "interventions": interventions,
    }


def get_patient_finding_summaries(
    patient_examination_ids: list[int],
) -> dict[int, list[PatientFindingHistoryData]]:
    """Load active clinical context for reports and cohorts in three queries."""
    summaries: dict[int, list[PatientFindingHistoryData]] = {}
    if not patient_examination_ids:
        return summaries
    findings = (
        PatientFinding.objects.filter(
            patient_examination_id__in=patient_examination_ids,
            is_active=True,
        )
        .select_related("finding")
        .order_by("patient_examination_id", "pk")
        .prefetch_related(
            Prefetch(
                "classifications",
                queryset=PatientFindingClassification.objects.filter(is_active=True)
                .select_related("classification", "classification_choice")
                .order_by("pk"),
            ),
            Prefetch(
                "interventions",
                queryset=PatientFindingIntervention.objects.filter(is_active=True)
                .select_related("intervention")
                .order_by("pk"),
            ),
        )
    )
    for finding in findings:
        finding_ref = cast(_PatientFindingSummaryLike, finding)
        summaries.setdefault(finding_ref.patient_examination_id, []).append(
            _serialize_patient_finding_summary(finding)
        )
    return summaries


def get_patient_examination_history_context(
    patient_examination: PatientExamination,
    *,
    limit: int = 5,
) -> PatientExaminationHistoryContextData:
    """
    Build a history payload for report rendering from existing records.

    Only strictly earlier examination dates establish prior knowledge. An
    undated current examination therefore has no historical context. This is
    read-only derived context, not an immutable snapshot of past clinical state.
    """
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("history limit must be an integer between 1 and 50.")
    patient = patient_examination.patient
    assert patient is not None, "PatientExamination must have an associated patient."
    patient_ref = cast(_IdentifiedLike, patient)
    patient_examination_ref = cast(_IdentifiedLike, patient_examination)

    identity = Q(patient=patient)
    if (
        not patient.is_real_person
        and patient.patient_hash
        and patient.patient_hash.strip()
        and patient.center_id is not None
    ):
        identity |= Q(
            patient__patient_hash=patient.patient_hash,
            patient__center_id=patient.center_id,
            patient__is_real_person=False,
        )
    prior_examinations: list[PatientExamination] = []
    if patient_examination.date_start is not None:
        prior_examinations = list(
            PatientExamination.objects.filter(
                identity, date_start__lt=patient_examination.date_start
            )
            .exclude(pk=patient_examination.pk)
            .select_related("examination")
            .order_by("-date_start", "-id")[:limit]
        )
    findings_by_examination = get_patient_finding_summaries(
        [row.pk for row in prior_examinations]
    )

    previous_examinations: list[PreviousPatientExaminationHistoryData] = []
    for patient_examination_row in prior_examinations:
        pe = cast(_PatientExaminationHistoryLike, patient_examination_row)
        previous_examinations.append(
            {
                "patient_examination_id": pe.id,
                "examination_id": pe.examination_id,
                "examination_name": _related_name(pe.examination),
                "date_start": _temporal_value(pe.date_start),
                "date_end": _temporal_value(pe.date_end),
                "findings": findings_by_examination.get(pe.id, []),
            }
        )

    return {
        "patient_id": patient_ref.id,
        "patient_examination_id": patient_examination_ref.id,
        "history_depth": limit,
        "previous_examinations": previous_examinations,
    }

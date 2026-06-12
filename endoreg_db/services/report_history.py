from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, cast

from django.db.models import Prefetch, QuerySet

from endoreg_db.helpers.model_ids import model_pk
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from lx_dtypes.models.contracts.patient_examination_report import (
    PatientExaminationHistoryContextData,
    PatientFindingClassificationHistoryData,
    PatientFindingHistoryData,
    PatientFindingInterventionHistoryData,
    PreviousPatientExaminationHistoryData,
)


class _IdentifiedLike(Protocol):
    id: int


class _HistoryRelatedQuery(Protocol):
    def select_related(self, *fields: str) -> Iterable[object]: ...


class _HistoryRelatedManager(Protocol):
    def filter(self, **kwargs: object) -> _HistoryRelatedQuery: ...


class _PatientFindingManager(Protocol):
    def all(self) -> Iterable[object]: ...


class _PatientFindingSummaryLike(Protocol):
    pk: int
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
    patient_findings: _PatientFindingManager


def _serialize_patient_finding_classification(
    row: object,
) -> PatientFindingClassificationHistoryData:
    pfc = cast(_PatientFindingClassificationSummaryLike, row)
    return {
        "id": model_pk(row),
        "classification_id": pfc.classification_id,
        "classification_choice_id": pfc.classification_choice_id,
        "classification_name": getattr(pfc.classification, "name", None),
        "classification_choice_name": getattr(
            pfc.classification_choice,
            "name",
            None,
        ),
        "subcategories": pfc.subcategories or {},
        "numerical_descriptors": pfc.numerical_descriptors or {},
    }


def _serialize_patient_finding_intervention(
    row: object,
) -> PatientFindingInterventionHistoryData:
    pfi = cast(_PatientFindingInterventionSummaryLike, row)
    return {
        "id": model_pk(row),
        "intervention_id": pfi.intervention_id,
        "intervention_name": getattr(pfi.intervention, "name", None),
        "state": pfi.state,
        "date": pfi.date,
        "time_start": pfi.time_start,
        "time_end": pfi.time_end,
    }


def _serialize_patient_finding_summary(
    patient_finding: PatientFinding,
) -> PatientFindingHistoryData:
    patient_finding_ref = cast(_PatientFindingSummaryLike, patient_finding)

    classifications = [
        _serialize_patient_finding_classification(row)
        for row in patient_finding_ref.classifications.filter(
            is_active=True
        ).select_related("classification", "classification_choice")
    ]
    interventions = [
        _serialize_patient_finding_intervention(row)
        for row in patient_finding_ref.interventions.filter(
            is_active=True
        ).select_related("intervention")
    ]

    return {
        "patient_finding_id": model_pk(patient_finding),
        "finding_id": patient_finding_ref.finding_id,
        "finding_name": getattr(patient_finding_ref.finding, "name", None),
        "classifications": classifications,
        "interventions": interventions,
    }


def get_patient_examination_history_context(
    patient_examination: PatientExamination,
    *,
    limit: int = 5,
) -> PatientExaminationHistoryContextData:
    """
    Build a history payload for report rendering from existing records.

    This is read-only derived context. It should not persist any report data.
    """
    patient = patient_examination.patient
    assert patient is not None, "PatientExamination must have an associated patient."
    patient_ref = cast(_IdentifiedLike, patient)
    patient_examination_ref = cast(_IdentifiedLike, patient_examination)

    prior_examinations: QuerySet[PatientExamination] = (
        PatientExamination.objects.filter(patient=patient)
        .exclude(pk=patient_examination.pk)
        .select_related("examination")
        .prefetch_related(
            Prefetch(
                "patient_findings",
                queryset=PatientFinding.objects.filter(is_active=True).select_related(
                    "finding"
                ),
            )
        )
        .order_by("-date_start", "-id")[:limit]
    )

    previous_examinations: list[PreviousPatientExaminationHistoryData] = []
    for patient_examination_row in prior_examinations:
        pe = cast(_PatientExaminationHistoryLike, patient_examination_row)
        findings = [
            _serialize_patient_finding_summary(cast(PatientFinding, patient_finding))
            for patient_finding in pe.patient_findings.all()
            if getattr(patient_finding, "is_active", True)
        ]
        previous_examinations.append(
            {
                "patient_examination_id": pe.id,
                "examination_id": pe.examination_id,
                "examination_name": getattr(pe.examination, "name", None),
                "date_start": pe.date_start,
                "date_end": pe.date_end,
                "findings": findings,
            }
        )

    return {
        "patient_id": patient_ref.id,
        "patient_examination_id": patient_examination_ref.id,
        "history_depth": limit,
        "previous_examinations": previous_examinations,
    }

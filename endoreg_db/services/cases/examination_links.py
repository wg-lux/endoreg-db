from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from django.db import models
from django.db.models import Prefetch

from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_examination_indication import (
    PatientExaminationIndication,
)
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.models.medical.patient.patient_finding_classification import (
    PatientFindingClassification,
)
from endoreg_db.models.medical.patient.patient_finding_intervention import (
    PatientFindingIntervention,
)
from endoreg_db.models.medical.patient.patient_lab_value import PatientLabValue
from endoreg_db.utils.links import ModelLinks

if TYPE_CHECKING:
    from endoreg_db.models.medical.examination.examination_indication import (
        ExaminationIndication,
        ExaminationIndicationClassificationChoice,
    )
    from endoreg_db.models.medical.finding.finding import Finding
    from endoreg_db.models.medical.finding.finding_classification import (
        FindingClassification,
        FindingClassificationChoice,
    )
    from endoreg_db.models.medical.finding.finding_intervention import (
        FindingIntervention,
    )


class PatientExaminationLinksNotPrefetchedError(RuntimeError):
    """Raised when link aggregation would otherwise trigger hidden ORM queries."""


def _relation_is_prefetched(instance: models.Model, relation_name: str) -> bool:
    raw_cache = getattr(instance, "_prefetched_objects_cache", None)
    if not isinstance(raw_cache, Mapping):
        return False
    cache = cast(Mapping[object, object], raw_cache)
    return relation_name in cache


def _field_is_selected(instance: models.Model, field_name: str) -> bool:
    return field_name in instance._state.fields_cache


def _raise_for_missing_prefetches(missing_relations: list[str]) -> None:
    if not missing_relations:
        return
    missing = ", ".join(sorted(set(missing_relations)))
    raise PatientExaminationLinksNotPrefetchedError(
        "build_patient_examination_links() requires "
        "load_patient_examination_for_links(); missing prefetches: "
        f"{missing}."
    )


def load_patient_examination_for_links(
    patient_examination_id: int,
) -> PatientExamination:
    """Load exactly the object graph consumed by the link builder."""
    classification_queryset = PatientFindingClassification.objects.filter(
        is_active=True
    ).select_related("classification", "classification_choice")
    intervention_queryset = PatientFindingIntervention.objects.filter(
        is_active=True
    ).select_related("intervention")
    finding_queryset = PatientFinding.objects.select_related(
        "finding"
    ).prefetch_related(
        Prefetch("classifications", queryset=classification_queryset),
        Prefetch("interventions", queryset=intervention_queryset),
    )
    indication_queryset = PatientExaminationIndication.objects.select_related(
        "examination_indication",
        "indication_choice",
    )

    return (
        PatientExamination.objects.select_related("patient", "examination")
        .prefetch_related(
            Prefetch("indications", queryset=indication_queryset),
            Prefetch(
                "patient__lab_values",
                queryset=PatientLabValue.objects.all(),
            ),
            Prefetch("patient_findings", queryset=finding_queryset),
        )
        .get(pk=patient_examination_id)
    )


def _missing_examination_prefetches(
    patient_examination: PatientExamination,
) -> list[str]:
    missing: list[str] = []
    if not _field_is_selected(patient_examination, "patient"):
        missing.append("patient")
    if not _field_is_selected(patient_examination, "examination"):
        missing.append("examination")
    if not _relation_is_prefetched(patient_examination, "indications"):
        missing.append("indications")
    if not _relation_is_prefetched(patient_examination, "patient_findings"):
        missing.append("patient_findings")
    return missing


def _prefetched_patient_findings(
    patient_examination: PatientExamination,
    missing_relations: list[str],
) -> list[PatientFinding]:
    patient_findings = list(patient_examination.patient_findings.all())
    for patient_finding in patient_findings:
        if not _relation_is_prefetched(patient_finding, "classifications"):
            missing_relations.append("patient_findings__classifications")
        if not _relation_is_prefetched(patient_finding, "interventions"):
            missing_relations.append("patient_findings__interventions")
    return patient_findings


def _indication_links(
    patient_examination: PatientExamination,
) -> tuple[
    list[ExaminationIndication],
    list[ExaminationIndicationClassificationChoice],
]:
    indications = list(patient_examination.indications.all())
    examination_indications = [
        item.examination_indication
        for item in indications
        if item.examination_indication is not None
    ]
    indication_choices = [
        item.indication_choice
        for item in indications
        if item.indication_choice is not None
    ]
    return examination_indications, indication_choices


def _finding_classification_rows(
    patient_findings: list[PatientFinding],
) -> list[PatientFindingClassification]:
    return [
        classification
        for finding in patient_findings
        for classification in finding.classifications.all()
    ]


def _finding_intervention_rows(
    patient_findings: list[PatientFinding],
) -> list[PatientFindingIntervention]:
    return [
        intervention
        for finding in patient_findings
        for intervention in finding.interventions.all()
    ]


def _finding_models(patient_findings: list[PatientFinding]) -> list[Finding]:
    return [finding.finding for finding in patient_findings]


def _classification_models(
    classification_rows: list[PatientFindingClassification],
) -> list[FindingClassification]:
    return [item.classification for item in classification_rows]


def _classification_choice_models(
    classification_rows: list[PatientFindingClassification],
) -> list[FindingClassificationChoice]:
    return [item.classification_choice for item in classification_rows]


def _intervention_models(
    intervention_rows: list[PatientFindingIntervention],
) -> list[FindingIntervention]:
    return [item.intervention for item in intervention_rows]


def build_patient_examination_links(
    patient_examination: PatientExamination,
) -> ModelLinks:
    """Build links without queries, requiring the canonical loader contract."""
    missing_relations = _missing_examination_prefetches(patient_examination)
    _raise_for_missing_prefetches(missing_relations)
    patient = patient_examination.patient
    if not _relation_is_prefetched(patient, "lab_values"):
        missing_relations.append("patient__lab_values")
    patient_findings = _prefetched_patient_findings(
        patient_examination,
        missing_relations,
    )
    _raise_for_missing_prefetches(missing_relations)
    examination_indications, indication_choices = _indication_links(patient_examination)
    classification_rows = _finding_classification_rows(patient_findings)
    intervention_rows = _finding_intervention_rows(patient_findings)
    return ModelLinks(
        patient_examinations=[patient_examination],
        examinations=(
            [patient_examination.examination]
            if patient_examination.examination is not None
            else []
        ),
        examination_indications=examination_indications,
        examination_indication_classification_choices=indication_choices,
        patient_lab_values=list(patient.lab_values.all()),
        patient_findings=patient_findings,
        findings=_finding_models(patient_findings),
        finding_classifications=_classification_models(classification_rows),
        finding_classification_choices=_classification_choice_models(
            classification_rows
        ),
        finding_interventions=_intervention_models(intervention_rows),
    )


__all__ = [
    "PatientExaminationLinksNotPrefetchedError",
    "build_patient_examination_links",
    "load_patient_examination_for_links",
]

from __future__ import annotations

from typing import Any

from django.db.models import Prefetch, QuerySet

from endoreg_db.models import PatientExamination, PatientFinding


def _serialize_patient_finding_summary(
    patient_finding: PatientFinding,
) -> dict[str, Any]:
    return {
        "patient_finding_id": patient_finding.pk,
        "finding_id": patient_finding.finding_id,
        "finding_name": getattr(patient_finding.finding, "name", None),
        "classifications": [
            {
                "id": pfc.pk,
                "classification_id": pfc.classification_id,
                "classification_choice_id": pfc.classification_choice_id,
                "classification_name": getattr(pfc.classification, "name", None),
                "classification_choice_name": getattr(
                    pfc.classification_choice, "name", None
                ),
                "subcategories": pfc.subcategories or {},
                "numerical_descriptors": pfc.numerical_descriptors or {},
            }
            for pfc in patient_finding.classifications.filter(
                is_active=True
            ).select_related("classification", "classification_choice")
        ],
        "interventions": [
            {
                "id": pfi.pk,
                "intervention_id": pfi.intervention_id,
                "intervention_name": getattr(pfi.intervention, "name", None),
                "state": pfi.state,
                "date": pfi.date,
                "time_start": pfi.time_start,
                "time_end": pfi.time_end,
            }
            for pfi in patient_finding.interventions.filter(
                is_active=True
            ).select_related("intervention")
        ],
    }


def get_patient_examination_history_context(
    patient_examination: PatientExamination,
    *,
    limit: int = 5,
) -> dict[str, Any]:
    """
    Build a history payload for report rendering from existing records.

    This is read-only derived context. It should not persist any report data.
    """
    patient = patient_examination.patient
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

    return {
        "patient_id": patient.id,
        "patient_examination_id": patient_examination.id,
        "history_depth": limit,
        "previous_examinations": [
            {
                "patient_examination_id": pe.id,
                "examination_id": pe.examination_id,
                "examination_name": getattr(pe.examination, "name", None),
                "date_start": pe.date_start,
                "date_end": pe.date_end,
                "findings": [
                    _serialize_patient_finding_summary(pf)
                    for pf in pe.patient_findings.all()
                    if getattr(pf, "is_active", True)
                ],
            }
            for pe in prior_examinations
        ],
    }

"""
Requirement guidance service for persisted PatientExamination state.

This module intentionally keeps the historical ``lookup_service`` import path for
backend callers that still import it, but the cache-backed lookup session logic
has been removed. The remaining responsibility is:

- loading a PatientExamination with the required prefetch graph
- resolving linked RequirementSets
- evaluating advisory requirement guidance for persisted examinations
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, cast

from django.db.models import Prefetch, QuerySet

from endoreg_db.models.medical.examination import ExaminationRequirementSet
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
from endoreg_db.models.requirement.requirement_set import RequirementSet

from .dtypes_requirement_service import (
    LOOKUP_REQUIREMENT_SOURCE_DTYPES,
    LOOKUP_REQUIREMENT_SOURCE_HYBRID_COMPARE,
    get_lookup_requirement_legacy_fallback_enabled,
    get_lookup_requirement_source,
    try_build_dtypes_requirement_guidance,
)
from .markov_prior_service import (
    DEFAULT_MARKOV_CONFIDENCE_THRESHOLD,
    propose_candidate_requirement_sets,
)
from .report_history import get_patient_examination_history_context

logger = logging.getLogger(__name__)

HYBRID_COMPARE_GUIDANCE_KEYS: tuple[str, ...] = (
    "requirements_by_set",
    "requirement_status",
    "requirement_set_status",
    "requirement_defaults",
    "classification_choices",
    "suggested_actions",
    "candidate_requirement_set_ids",
    "candidate_requirement_set_confidence",
)


def _indication_prefetch_bundle() -> tuple[Prefetch, ...]:
    return (
        Prefetch(
            "indications",
            queryset=PatientExaminationIndication.objects.select_related(
                "examination_indication",
                "indication_choice",
            ),
        ),
    )


def _lab_value_prefetch_bundle() -> tuple[Prefetch, ...]:
    return (
        Prefetch(
            "patient__lab_values",
            queryset=PatientLabValue.objects.select_related(
                "lab_value",
                "unit",
                "sample",
            ),
        ),
    )


def _finding_prefetch_bundle() -> tuple[Prefetch, ...]:
    return (
        Prefetch(
            "patient_findings",
            queryset=PatientFinding.objects.select_related("finding").prefetch_related(
                Prefetch(
                    "classifications",
                    queryset=PatientFindingClassification.objects.select_related(
                        "classification",
                        "classification_choice",
                    ),
                ),
                Prefetch(
                    "interventions",
                    queryset=PatientFindingIntervention.objects.select_related(
                        "intervention",
                    ),
                ),
            ),
        ),
    )


def _requirement_set_prefetch_bundle() -> tuple[Prefetch, ...]:
    return (
        Prefetch(
            "examination__exam_reqset_links",
            queryset=ExaminationRequirementSet.objects.only(
                "id", "name", "enabled_by_default"
            ),
        ),
        Prefetch(
            "examination__exam_reqset_links__requirement_set",
            queryset=RequirementSet.objects.select_related(
                "requirement_set_type"
            ).prefetch_related(
                "requirements",
                "links_to_sets",
                "links_to_sets__requirements",
                "links_to_sets__requirement_set_type",
            ),
        ),
    )


def _divergent_lookup_keys(
    *,
    legacy_payload: dict[str, Any],
    dtypes_payload: dict[str, Any],
    keys: Sequence[str],
) -> list[str]:
    sentinel = object()
    divergent: list[str] = []
    for key in keys:
        if legacy_payload.get(key, sentinel) != dtypes_payload.get(key, sentinel):
            divergent.append(key)
    return divergent


def _log_hybrid_compare_divergence(
    *,
    context: str,
    patient_examination_id: int | None,
    divergent_keys: Sequence[str],
) -> None:
    if not divergent_keys:
        return
    logger.warning(
        "requirement guidance hybrid_compare divergence context=%s pe_id=%s keys=%s",
        context,
        patient_examination_id,
        list(divergent_keys),
    )


def _evaluate_patient_exam_requirement_guidance_legacy(
    pe: PatientExamination,
    *,
    selected_requirement_set_ids: Optional[List[int]] = None,
    user_tags: Optional[List[str]] = None,
    use_history_priors: bool = True,
) -> Dict[str, Any]:
    all_rs_for_exam = list(requirement_sets_for_patient_exam(pe, user_tags=user_tags))

    patient_finding_names: List[str] = []
    try:
        for patient_finding in pe.patient_findings.all():
            finding_name = getattr(
                getattr(patient_finding, "finding", None), "name", None
            )
            if isinstance(finding_name, str) and finding_name:
                patient_finding_names.append(finding_name)
    except Exception:
        patient_finding_names = []

    history_context: Dict[str, Any] | None = None
    history_tokens: List[str] = []
    if use_history_priors:
        try:
            history_context = get_patient_examination_history_context(pe, limit=5)
            for previous_exam in history_context.get("previous_examinations", []) or []:
                if not isinstance(previous_exam, dict):
                    continue
                exam_name = previous_exam.get("examination_name")
                if isinstance(exam_name, str) and exam_name:
                    history_tokens.append(exam_name)
                for finding_item in previous_exam.get("findings", []) or []:
                    if not isinstance(finding_item, dict):
                        continue
                    finding_name = finding_item.get("finding_name")
                    if isinstance(finding_name, str) and finding_name:
                        history_tokens.append(finding_name)
        except Exception as exc:
            logger.debug(
                "Failed to build history context for requirement guidance (pe=%s): %s",
                pe.id,
                exc,
            )
            history_context = None
            history_tokens = []

    prior_result = propose_candidate_requirement_sets(
        patient_finding_names=patient_finding_names,
        examination_name=getattr(pe.examination, "name", None),
        requirement_sets=all_rs_for_exam,
        history_context=history_context,
        history_tokens=history_tokens,
    )

    selected_rs_ids = selected_requirement_set_ids or []
    if selected_rs_ids:
        eval_rs_ids = set(selected_rs_ids)
    elif (
        prior_result.confidence >= DEFAULT_MARKOV_CONFIDENCE_THRESHOLD
        and prior_result.candidate_requirement_set_ids
    ):
        eval_rs_ids = set(prior_result.candidate_requirement_set_ids)
    else:
        eval_rs_ids = {rs.id for rs in all_rs_for_exam}

    rs_objs = [rs for rs in all_rs_for_exam if rs.id in eval_rs_ids]

    requirements_by_set = {
        rs.id: [{"id": r.id, "name": r.name} for r in rs.requirements.all()]
        for rs in rs_objs
    }

    requirement_status: Dict[str, bool] = {}
    set_status: Dict[str, bool] = {}
    suggested_actions: Dict[str, List[Dict[str, Any]]] = {}
    req_defaults: Dict[str, Any] = {}
    cls_choices: Dict[str, Any] = {}

    for rs in rs_objs:
        req_results = []
        for requirement in rs.requirements.all():
            ok = bool(requirement.evaluate(pe, mode="strict"))
            requirement_status[str(requirement.id)] = ok
            req_results.append(ok)

            defaults: list[Any] = getattr(
                requirement, "default_findings", lambda patient_exam: []
            )(pe)
            choices: list[Any] = getattr(
                requirement, "classification_choices", lambda patient_exam: []
            )(pe)
            if defaults:
                req_defaults[str(requirement.id)] = defaults
            if choices:
                cls_choices[str(requirement.id)] = choices

            if not ok:
                acts: List[Dict[str, Any]] = []
                for default in defaults or []:
                    acts.append(
                        {
                            "type": "add_finding",
                            "finding_id": default.get("finding_id"),
                            "classification_ids": default.get("classification_ids")
                            or [],
                            "note": "default",
                        }
                    )
                if "PatientExamination" in [
                    model.__name__ for model in requirement.expected_models
                ]:
                    acts.append({"type": "edit_patient", "fields": ["gender", "dob"]})
                if acts:
                    suggested_actions[str(requirement.id)] = acts

        set_status[str(rs.id)] = (
            rs.eval_function(req_results) if rs.eval_function else all(req_results)
        )

    return {
        "requirements_by_set": requirements_by_set,
        "requirement_status": requirement_status,
        "requirement_set_status": set_status,
        "requirement_defaults": req_defaults,
        "classification_choices": cls_choices,
        "suggested_actions": suggested_actions,
        "candidate_requirement_set_ids": prior_result.candidate_requirement_set_ids,
        "candidate_requirement_set_confidence": prior_result.confidence,
        "selected_requirement_set_ids": list(selected_rs_ids),
        "history_context": history_context or {},
        "advisory_only": True,
    }


def evaluate_patient_exam_requirement_guidance(
    pe: PatientExamination,
    *,
    selected_requirement_set_ids: Optional[List[int]] = None,
    user_tags: Optional[List[str]] = None,
    use_history_priors: bool = True,
) -> Dict[str, Any]:
    """
    Evaluate advisory requirement guidance for a persisted PatientExamination.
    """
    requirement_source = get_lookup_requirement_source()
    if requirement_source in (
        LOOKUP_REQUIREMENT_SOURCE_DTYPES,
        LOOKUP_REQUIREMENT_SOURCE_HYBRID_COMPARE,
    ):
        dtypes_guidance = try_build_dtypes_requirement_guidance(
            pe=cast(Any, pe),
            selected_requirement_set_ids=selected_requirement_set_ids,
            user_tags=user_tags,
            use_history_priors=use_history_priors,
        )
        if requirement_source == LOOKUP_REQUIREMENT_SOURCE_DTYPES:
            if dtypes_guidance is not None:
                return dtypes_guidance
            if get_lookup_requirement_legacy_fallback_enabled():
                logger.warning(
                    "dtypes guidance unavailable; emergency legacy fallback enabled for pe_id=%s",
                    pe.id,
                )
                return _evaluate_patient_exam_requirement_guidance_legacy(
                    pe,
                    selected_requirement_set_ids=selected_requirement_set_ids,
                    user_tags=user_tags,
                    use_history_priors=use_history_priors,
                )
            raise ValueError(
                "dtypes guidance unavailable and LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED is false"
            )

        legacy_guidance = _evaluate_patient_exam_requirement_guidance_legacy(
            pe,
            selected_requirement_set_ids=selected_requirement_set_ids,
            user_tags=user_tags,
            use_history_priors=use_history_priors,
        )
        if dtypes_guidance is None:
            logger.warning(
                "hybrid_compare dtypes guidance unavailable; using legacy guidance for pe_id=%s",
                pe.id,
            )
            return legacy_guidance

        divergent_keys = _divergent_lookup_keys(
            legacy_payload=legacy_guidance,
            dtypes_payload=dtypes_guidance,
            keys=HYBRID_COMPARE_GUIDANCE_KEYS,
        )
        _log_hybrid_compare_divergence(
            context="guidance",
            patient_examination_id=pe.id,
            divergent_keys=divergent_keys,
        )
        return dtypes_guidance

    return _evaluate_patient_exam_requirement_guidance_legacy(
        pe,
        selected_requirement_set_ids=selected_requirement_set_ids,
        user_tags=user_tags,
        use_history_priors=use_history_priors,
    )


def load_patient_exam_for_eval(pk: int) -> PatientExamination:
    prefetches = (
        *_indication_prefetch_bundle(),
        *_lab_value_prefetch_bundle(),
        *_finding_prefetch_bundle(),
        *_requirement_set_prefetch_bundle(),
    )
    return (
        PatientExamination.objects.select_related("patient", "examination")
        .prefetch_related(*prefetches)
        .get(pk=pk)
    )


def requirement_sets_for_patient_exam(
    pe: PatientExamination, user_tags: Optional[List[str]] = None
) -> QuerySet:
    if not pe or not pe.examination:
        return RequirementSet.objects.none()

    req_sets = pe.examination.exam_reqset_links.values_list(
        "requirement_set", flat=True
    )
    qs = RequirementSet.objects.filter(pk__in=req_sets)
    if user_tags:
        qs = qs.filter(tags__name__in=user_tags).distinct()
    return qs

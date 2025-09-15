# app/services/evaluation.py
from __future__ import annotations
from typing import Dict, List, Set, Tuple
from django.db.models import Prefetch

from endoreg_db.schemas.examination_evaluation import ExaminationEvalReport, RequirementSetEval, RequirementEval
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.requirement.requirement_set import RequirementSet
import endoreg_db.services.lookup_service

def _get_requirement_sets_for_exam(exam: PatientExamination) -> List[RequirementSet]:
    """
    Decide how an examination maps to requirement sets.
    Adjust this to your actual relation:
    - maybe exam.type.requirement_sets
    - or via information_sources
    - or a direct M2M on Examination
    """
    # The new loader prefetches via `examination.exam_reqset_links`.
    # We extract the RequirementSet from each link.
    sets = []
    if hasattr(exam, "examination") and hasattr(exam.examination, "exam_reqset_links"):
        for link in exam.examination.exam_reqset_links.all():
            if hasattr(link, "requirement_set"):
                sets.append(link.requirement_set)
    return sets

def _eval_requirement(requirement, input_object, mode="loose") -> Tuple[bool, str | None]:
    """
    Evaluate a single Requirement and return (bool, message).
    The `message` can be None or contain a short explanation.
    """
    ok = requirement.evaluate(input_object, mode=mode)
    msg = None
    # If your Requirement has a reason/explanation API, inject it here.
    # Example: msg = requirement.last_reason if hasattr(requirement, "last_reason") else None
    return ok, msg

def _reduce_bools(bools: List[bool], set_type_name: str | None) -> bool:
    from endoreg_db.models.requirement.requirement_set import REQUIREMENT_SET_TYPE_FUNCTION_LOOKUP
    func = REQUIREMENT_SET_TYPE_FUNCTION_LOOKUP.get(set_type_name or "all", all)
    return bool(func(bools))

def _eval_set_tree(root: RequirementSet, input_object, visited: Set[int]) -> RequirementSetEval:
    """
    Recursively evaluate a RequirementSet node and linked children.
    Protect against cycles with visited set.
    """
    if root.pk in visited:
        # Cycle detected -> treat as already evaluated (neutral element for AND is True; but better: skip node)
        # We skip to avoid infinite recursion; AND/OR exact treatment depends on your business semantics.
        return RequirementSetEval(
            id=root.pk,
            name=root.name,
            type=(root.requirement_set_type.name if root.requirement_set_type else None),
            is_satisfied=True,
            requirements=[],
            linked_sets=[]
        )

    visited.add(root.pk)

    # Evaluate direct requirements
    req_evals: List[RequirementEval] = []
    for r in root.requirements.all():
        ok, msg = _eval_requirement(r, input_object)
        req_evals.append(
            RequirementEval(
                id=r.pk,
                name=getattr(r, "name", f"Requirement {r.pk}"),
                description=getattr(r, "description", None),
                satisfied=ok,
                message=msg,
            )
        )

    # Evaluate linked sets
    child_evals: List[RequirementSetEval] = []
    for child in root.links_to_sets.all():
        child_evals.append(_eval_set_tree(child, input_object, visited))

    # Combine booleans
    bools = [re.satisfied for re in req_evals] + [ce.is_satisfied for ce in child_evals]
    set_type_name = root.requirement_set_type.name if root.requirement_set_type else "all"
    satisfied = _reduce_bools(bools, set_type_name)

    return RequirementSetEval(
        id=root.pk,
        name=root.name,
        type=set_type_name,
        is_satisfied=satisfied,
        requirements=req_evals,
        linked_sets=child_evals,
    )
    


def evaluate_examination(request: dict) -> ExaminationEvalReport:
    """
    Communicates with: components/RequirementGenerator
    Evaluates a PatientExamination by its Lookup. The frontend sends this structure:
    
    
        requirement_set_ids: plainRequirementSetIds,
        lookup_token: lookupToken,
        patient_examination_id: patientExaminationId
    };
    
    And expects a response to be processed like this:

            const response = await axiosInstance.post('/api/evaluate-requirements/', payload);
            const results = response.data.results || [];
            const summary = response.data.summary || {};

            // Update evaluation results
            plainRequirementSetIds.forEach((setId: number) => {
                evaluationResults.value[setId] = results.filter((r: RequirementEvaluationResult) =>
                    summary[setId]?.requirements.includes(r.requirement_name)
                );
            });
    """
    exam_id = request.get("patient_examination_id")
    if not exam_id:
        return ExaminationEvalReport(
            examination_id=None,
            summary={"is_satisfied": True, "failed_count": 0, "total_sets": 0},
            sets=[],
            errors=["No patient_examination_id provided in request."]
        )
    
    # Use the dedicated loader function from the lookup service.
    exam = endoreg_db.services.lookup_service.load_patient_exam_for_eval(pk=exam_id)

    # This object (exam) is the "input_object" for requirement.evaluate(...)
    sets = _get_requirement_sets_for_exam(exam)

    visited: Set[int] = set()
    set_evals: List[RequirementSetEval] = [_eval_set_tree(s, exam, visited) for s in sets]

    # Aggregate summary
    overall = all(se.is_satisfied for se in set_evals) if set_evals else True
    failed = sum(1 for se in set_evals if not se.is_satisfied)

    return ExaminationEvalReport(
        examination_id=exam.pk,
        summary={"is_satisfied": overall, "failed_count": failed, "total_sets": len(set_evals)},
        sets=set_evals,
        errors=[],  # fill with any global issues you detect
    )

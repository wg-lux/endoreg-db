# services/lookup_service.py
from __future__ import annotations
from typing import Dict, Any, List
from django.db.models import Prefetch
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.examination import ExaminationRequirementSet  # import the ERS hub
from endoreg_db.models.requirement.requirement_set import RequirementSet
from .lookup_store import LookupStore


def load_patient_exam_for_eval(pk: int) -> PatientExamination:
    """
    Fetch PatientExamination with everything needed for evaluation,
    following the *Examination → ExaminationRequirementSet → RequirementSet* graph.
    """
    return (
        PatientExamination.objects
        .select_related("patient", "examination")
        .prefetch_related(
            "patient_findings",
            # Prefetch ERS groups on the Examination…
            Prefetch(
                "examination__exam_reqset_links",
                queryset=ExaminationRequirementSet.objects.only("id", "name", "enabled_by_default"),
            ),
            # …and the RequirementSets reachable via those ERS groups.
            Prefetch(
                "examination__exam_reqset_links__requirement_set",
                queryset=(
                    RequirementSet.objects
                    .select_related("requirement_set_type")
                    .prefetch_related(
                        "requirements",
                        "links_to_sets",
                        "links_to_sets__requirements",
                        "links_to_sets__requirement_set_type",
                    )
                ),
            ),
        )
        .get(pk=pk)
    )


def requirement_sets_for_patient_exam(pe: PatientExamination) -> List[RequirementSet]:
    """
    Correctly resolve RequirementSets for a PE via ERS hub:
        RequirementSet.objects.filter(reqset_exam_links__examinations=exam)
    """
    exam = pe.examination
    if not exam:
        return []
    return list(
        RequirementSet.objects
        .filter(reqset_exam_links__examinations=exam)
        .select_related("requirement_set_type")
        .prefetch_related("requirements")
        .distinct()
    )

def build_initial_lookup(pe: PatientExamination) -> Dict[str, Any]:
    """
    Build the initial lookup dict you will return to the client.
    Keep keys small and stable; values must be JSON-serializable.
    """
    # Available + required findings
    available_findings = [f.id for f in pe.examination.get_available_findings()] if pe.examination else []
    required_findings: List[int] = []  # fill by scanning requirements below

    # Requirement sets: ids + meta
    rs_objs = requirement_sets_for_patient_exam(pe)
    requirement_sets = [
        {
            "id": rs.id,
            "name": rs.name,
            "type": rs.requirement_set_type.name if rs.requirement_set_type else "all",
        }
        for rs in rs_objs
    ]

    # Requirement-level defaults and classification choices (skeleton)
    # You said: each Requirement can provide default findings and addable choices
    req_defaults: Dict[str, Any] = {}
    cls_choices: Dict[str, Any] = {}

    for rs in rs_objs:
        for req in rs.requirements.all():
            # You’ll implement these helpers on Requirement
            defaults = getattr(req, "default_findings", lambda pe: [])(pe)
            choices = getattr(req, "classification_choices", lambda pe: [])(pe)
            if defaults:
                req_defaults[str(req.id)] = defaults  # list of {finding_id, payload...}
                required_findings.extend([d.get("finding_id") for d in defaults if "finding_id" in d])
            if choices:
                cls_choices[str(req.id)] = choices   # list of {classification_id, label, ...}

    # De-dup required
    required_findings = sorted(set(required_findings))

    return {
        "patient_examination_id": pe.id,
        "requirement_sets": requirement_sets,
        "availableFindings": available_findings,
        "requiredFindings": required_findings,
        "requirementDefaults": req_defaults,
        "classificationChoices": cls_choices,
        # You can add “selectedRequirementSetIds” as the user makes choices
    }

def create_lookup_token_for_pe(pe_id: int) -> str:
    pe = load_patient_exam_for_eval(pe_id)
    token = LookupStore().init(build_initial_lookup(pe))
    return token

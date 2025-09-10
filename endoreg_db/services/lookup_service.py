# services/lookup_service.py
from __future__ import annotations
from typing import Dict, Any, List
from django.db.models import Prefetch
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.examination import ExaminationRequirementSet
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
        # New fields for expanded lookup payload
        "requirementsBySet": {},  # Will be populated when requirement sets are selected
        "requirementStatus": {},  # Status of each requirement (satisfied/unsatisfied)
        "requirementSetStatus": {},  # Status of each requirement set
        "suggestedActions": {},  # Suggested actions to satisfy requirements
        # You can add "selectedRequirementSetIds" as the user makes choices
    }

def create_lookup_token_for_pe(pe_id: int) -> str:
    pe = load_patient_exam_for_eval(pe_id)
    token = LookupStore().init(build_initial_lookup(pe))
    return token

def recompute_lookup(token: str) -> Dict[str, Any]:
    import logging
    logger = logging.getLogger(__name__)
    
    store = LookupStore(token=token)
    
    # Simple reentrancy guard using data
    data = store.get_all()
    if data.get('_recomputing'):
        logger.warning(f"Recompute already in progress for token {token}, skipping")
        return {}
    
    store.set('_recomputing', True)
    
    try:
        # First validate and attempt to recover corrupted data
        validated_data = store.validate_and_recover_data(token)
        if validated_data is None:
            logger.error(f"No lookup data found for token {token}")
            raise ValueError(f"No lookup data found for token {token}")
        
        data = validated_data
        logger.debug(f"Recomputing lookup for token {token}, data keys: {list(data.keys())}")
        
        # Check if required data exists
        if "patient_examination_id" not in data:
            logger.error(f"Invalid lookup data for token {token}: missing patient_examination_id. Data: {data}")
            raise ValueError(f"Invalid lookup data for token {token}: missing patient_examination_id")
        
        if not data.get("patient_examination_id"):
            logger.error(f"Invalid lookup data for token {token}: patient_examination_id is empty. Data: {data}")
            raise ValueError(f"Invalid lookup data for token {token}: patient_examination_id is empty")

        pe_id = data["patient_examination_id"]
        logger.debug(f"Loading patient examination {pe_id} for token {token}")
        
        try:
            pe = load_patient_exam_for_eval(pe_id)
        except Exception as e:
            logger.error(f"Failed to load patient examination {pe_id} for token {token}: {e}")
            raise ValueError(f"Failed to load patient examination {pe_id}: {e}")

        selected_rs_ids: List[int] = data.get("selectedRequirementSetIds", [])
        logger.debug(f"Selected requirement set IDs for token {token}: {selected_rs_ids}")
        
        rs_objs = [rs for rs in requirement_sets_for_patient_exam(pe) if rs.id in selected_rs_ids]
        logger.debug(f"Found {len(rs_objs)} requirement set objects for token {token}")

        # 1) requirements grouped by set (already prefetched in load func)
        requirements_by_set = {
            rs.id: [ {"id": r.id, "name": r.name} for r in rs.requirements.all() ]
            for rs in rs_objs
        }

        # 2) status per requirement + set status
        requirement_status: Dict[str, bool] = {}
        set_status: Dict[str, bool] = {}
        for rs in rs_objs:
            req_results = []
            for r in rs.requirements.all():
                ok = bool(r.evaluate(pe, mode="strict"))  # or "loose" if you prefer
                requirement_status[str(r.id)] = ok
                req_results.append(ok)
            set_status[str(rs.id)] = rs.eval_function(req_results) if rs.eval_function else all(req_results)

        # 3) suggestions per requirement (defaults + classification choices you already expose)
        suggested_actions: Dict[str, List[Dict[str, Any]]] = {}
        req_defaults: Dict[str, Any] = {}
        cls_choices: Dict[str, Any] = {}

        for rs in rs_objs:
            for r in rs.requirements.all():
                defaults = getattr(r, "default_findings", lambda pe: [])(pe)  # [{finding_id, payload...}]
                choices  = getattr(r, "classification_choices", lambda pe: [])(pe)  # [{classification_id, label,...}]
                if defaults:
                    req_defaults[str(r.id)] = defaults
                if choices:
                    cls_choices[str(r.id)] = choices

                if not requirement_status.get(str(r.id), False):
                    # turn default proposals into explicit UI actions
                    acts = []
                    for d in defaults or []:
                        acts.append({
                            "type": "add_finding",
                            "finding_id": d.get("finding_id"),
                            "classification_ids": d.get("classification_ids") or [],
                            "note": "default"
                        })
                    # If r expects patient edits, add an edit action hint
                    if "PatientExamination" in [m.__name__ for m in r.expected_models]:
                        acts.append({"type": "edit_patient", "fields": ["gender", "dob"]})  # example
                    if acts:
                        suggested_actions[str(r.id)] = acts

        # 4) (optional) staged changes simulation hook (see §3)
        # staged = data.get("staged", {})
        # if you implement server-side simulation later, adjust requirement_status with staged result here

        updates = {
            "requirementsBySet": requirements_by_set,
            "requirementStatus": requirement_status,
            "requirementSetStatus": set_status,
            "requirementDefaults": req_defaults,         # keep your existing key
            "classificationChoices": cls_choices,        # keep your existing key
            "suggestedActions": suggested_actions,       # new
        }
        
        logger.debug(f"Updating store for token {token} with {len(updates)} update keys")
        
        # Only write if changed (idempotent)
        prev_derived = store.get_many(list(updates.keys()))
        if prev_derived != updates:
            store.set_many(updates)  # <-- does NOT call recompute
            logger.debug(f"Derived data changed, updated store for token {token}")
        else:
            logger.debug(f"Derived data unchanged, skipping store update for token {token}")
        
        store.mark_recompute_done()
        return updates
    finally:
        store.set('_recomputing', False)

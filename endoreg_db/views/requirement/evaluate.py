from endoreg_db.models.requirement.requirement import Requirement
from endoreg_db.views.requirement.requirement_utils import safe_evaluate_requirement
from endoreg_db.models.requirement.requirement_set import RequirementSet
from endoreg_db.models.requirement.requirement_evaluation.evaluate_with_dependencies import (
    evaluate_requirement_sets_with_dependencies,  # if you export it there, otherwise re-declare in view
)
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
import json
import logging

logger = logging.getLogger(__name__)


@api_view(["POST"])
def evaluate_requirements(request):
    """
    Evaluate requirements (all selected sets) and always return 200 with structured results.
    """
    payload = request.data or {}
    req_set_ids = payload.get("requirement_set_ids")
    pe_id = payload.get("patient_examination_id")

    results: list[dict] = []
    errors: list[str] = []
    sets_evaluated = 0
    requirements_evaluated = 0

    # ---- basic validation (still 200 on failure)
    if not pe_id:
        msg = "patient_examination_id is required"
        errors.append(msg)
        logger.warning("evaluate_requirements: %s; payload=%s", msg, payload)
        return Response(
            {
                "ok": False,
                "errors": errors,
                "meta": {
                    "patientExaminationId": None,
                    "setsEvaluated": 0,
                    "requirementsEvaluated": 0,
                    "status": "failed",
                },
                "results": [],
            },
            status=status.HTTP_200_OK,
        )

    # ---- fetch PatientExamination
    try:
        pe = PatientExamination.objects.select_related("patient").get(id=pe_id)
    except PatientExamination.DoesNotExist:
        msg = f"PatientExamination with id {pe_id} does not exist"
        errors.append(msg)
        logger.warning("evaluate_requirements: %s", msg)
        return Response(
            {
                "ok": False,
                "errors": errors,
                "meta": {
                    "patientExaminationId": pe_id,
                    "setsEvaluated": 0,
                    "requirementsEvaluated": 0,
                    "status": "failed",
                },
                "results": [],
            },
            status=status.HTTP_200_OK,
        )
    except Exception as e:
        msg = f"Unexpected error retrieving PatientExamination {pe_id}: {e}"
        errors.append(msg)
        logger.exception("evaluate_requirements: %s", msg)
        return Response(
            {
                "ok": False,
                "errors": errors,
                "meta": {
                    "patientExaminationId": pe_id,
                    "setsEvaluated": 0,
                    "requirementsEvaluated": 0,
                    "status": "failed",
                },
                "results": [],
            },
            status=status.HTTP_200_OK,
        )

    # ---- determine requirement sets
    try:
        q = RequirementSet.objects.prefetch_related("requirements")
        if req_set_ids:
            q = q.filter(id__in=req_set_ids)

        requirement_sets = list(q)
        sets_evaluated = len(requirement_sets)

        if req_set_ids and sets_evaluated == 0:
            msg = f"No RequirementSets found for IDs: {req_set_ids}"
            errors.append(msg)
            logger.warning("evaluate_requirements: %s", msg)
    except Exception as e:
        msg = f"Error loading RequirementSets: {e}"
        errors.append(msg)
        logger.exception("evaluate_requirements: %s", msg)
        return Response(
            {
                "ok": False,
                "errors": errors,
                "meta": {
                    "patientExaminationId": pe_id,
                    "setsEvaluated": 0,
                    "requirementsEvaluated": 0,
                    "status": "failed",
                },
                "results": [],
            },
            status=status.HTTP_200_OK,
        )

    # nothing to evaluate → still return 200
    if not requirement_sets:
        response_payload = {
            "ok": len(errors) == 0,
            "errors": errors,
            "meta": {
                "patientExaminationId": pe_id,
                "setsEvaluated": 0,
                "requirementsEvaluated": 0,
                "status": "failed" if errors else "ok",
            },
            "results": [],
        }
        return Response(response_payload, status=status.HTTP_200_OK)

    # mapping from IDs to objects for later lookup
    sets_by_id: dict[int, RequirementSet] = {s.id: s for s in requirement_sets}

    # ---- main evaluation with set dependencies
    try:
        # returns: { set_id: { req_id: (status, details) } }
        set_results = evaluate_requirement_sets_with_dependencies(
            requirement_sets,
            pe.patient,
            mode="strict",
        )

        for set_id, req_dict in set_results.items():
            req_set = sets_by_id.get(set_id)
            set_name = (
                getattr(req_set, "name", str(set_id))
                if req_set is not None
                else str(set_id)
            )

            for req_id, (status_value, details) in req_dict.items():
                try:
                    requirement_obj = Requirement.objects.get(id=req_id)
                    req_name = getattr(requirement_obj, "name", f"#{req_id}")
                except Requirement.DoesNotExist:
                    requirement_obj = None
                    req_name = f"#{req_id}"

                # map RequirementStatus → met + error
                if status_value == "PASSED":
                    met = True
                    error_str = None
                elif status_value in ("FAILED", "BLOCKED"):
                    met = False
                    error_str = None
                else:  # "ERROR"
                    met = False
                    error_str = "Technischer Fehler bei der Auswertung"

                # normalize details to string
                if isinstance(details, str):
                    details_str = details
                else:
                    try:
                        details_str = json.dumps(
                            details, ensure_ascii=False, default=str
                        )
                    except Exception:
                        details_str = str(details)

                # default fallback text if details are empty
                if not details_str:
                    details_str = (
                        "Voraussetzung erfüllt"
                        if met
                        else "Voraussetzung nicht erfüllt"
                    )

                if status_value == "ERROR":
                    # add a high-level error for meta if there was an internal error
                    msg = (
                        f"Technischer Fehler bei der Auswertung von "
                        f"Voraussetzung '{req_name}' in Set '{set_name}'."
                    )
                    errors.append(msg)

                results.append(
                    {
                        "requirement_set_id": set_id,
                        "requirement_set_name": set_name,
                        "requirement_name": req_name,
                        "met": bool(met),
                        "details": details_str,
                        "error": error_str,
                        "status": status_value,
                    }
                )
                requirements_evaluated += 1

    except Exception as e:
        # hard failure of the orchestrator → log and fall back to per-requirement evaluation
        msg = f"Unerwarteter Fehler bei der gruppenbasierten Bewertung: {e}"
        errors.append(msg)
        logger.exception("evaluate_requirements: %s", msg)

        for req_set in requirement_sets:
            for req in req_set.requirements.all():
                met, details, error = safe_evaluate_requirement(
                    req, pe.patient, mode="strict"
                )
                # normalize details to string
                if not isinstance(details, str):
                    try:
                        details = json.dumps(details, ensure_ascii=False, default=str)
                    except Exception:
                        details = str(details)

                if not details:
                    details = (
                        "Voraussetzung erfüllt"
                        if met
                        else "Voraussetzung nicht erfüllt"
                    )

                results.append(
                    {
                        "requirement_set_id": req_set.id,
                        "requirement_set_name": getattr(
                            req_set, "name", str(req_set.id)
                        ),
                        "requirement_name": getattr(req, "name", "unknown"),
                        "met": bool(met),
                        "details": details,
                        "error": error,
                        "status": "PASSED" if met else "FAILED",
                    }
                )
                requirements_evaluated += 1

    # ---- response meta & status summary
    any_errors = len(errors) > 0
    if not requirement_sets:
        status_label = "failed"
    elif any_errors and len(results) > 0:
        status_label = "partial"
    else:
        status_label = "ok"

    response_payload = {
        "ok": not any_errors,
        "errors": errors,
        "meta": {
            "patientExaminationId": pe_id,
            "setsEvaluated": sets_evaluated,
            "requirementsEvaluated": requirements_evaluated,
            "status": status_label,
        },
        "results": results,
    }

    return Response(response_payload, status=status.HTTP_200_OK)

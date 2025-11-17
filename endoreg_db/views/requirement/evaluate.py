from endoreg_db.models.requirement.requirement import Requirement
from endoreg_db.views.requirement.requirement_utils import safe_evaluate_requirement
from endoreg_db.models.requirement.requirement_set import RequirementSet
from endoreg_db.models.medical.patient.patient_examination import PatientExamination

import json
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
import logging

logger = logging.getLogger(__name__)
@api_view(['POST'])
def evaluate_requirements(request):
    """
    Evaluate requirements and always return 200 with structured results.
    Payload:
    {
      "requirement_set_ids": [<int>, ...],  // optional; evaluates all if omitted
      "patient_examination_id": <int>       // required
    }

    Response (HTTP 200 always):
    {
      "ok": <bool>,                  // false if any errors occurred
      "errors": [<str>, ...],        // high-level problems (e.g. invalid input, missing PE)
      "meta": {
        "patientExaminationId": <int|null>,
        "setsEvaluated": <int>,
        "requirementsEvaluated": <int>,
        "status": "ok" | "partial" | "failed"
      },
      "results": [
        {
          "requirement_set_id": <int>,
          "requirement_set_name": <str>,
          "requirement_name": <str>,
          "met": <bool>,
          "details": <str>,          // always a string for UI display
          "error": <str|null>        // per-item error if evaluation failed
        }
      ]
    }
    """
    payload = request.data or {}
    req_set_ids = payload.get("requirement_set_ids")
    pe_id = payload.get("patient_examination_id")

    results = []
    errors = []
    sets_evaluated = 0
    requirements_evaluated = 0

    # ---- basic validation (still 200 on failure)
    if not pe_id:
        msg = "patient_examination_id is required"
        errors.append(msg)
        logger.warning("evaluate_requirements: %s; payload=%s", msg, payload)
        return Response({
            "ok": False,
            "errors": errors,
            "meta": {
                "patientExaminationId": None,
                "setsEvaluated": 0,
                "requirementsEvaluated": 0,
                "status": "failed",
            },
            "results": []
        }, status=status.HTTP_200_OK)

    # ---- fetch PatientExamination
    try:
        pe = (PatientExamination.objects
              .select_related("patient")
              .get(id=pe_id))
    except PatientExamination.DoesNotExist:
        msg = f"PatientExamination with id {pe_id} does not exist"
        errors.append(msg)
        logger.warning("evaluate_requirements: %s", msg)
        return Response({
            "ok": False,
            "errors": errors,
            "meta": {
                "patientExaminationId": pe_id,
                "setsEvaluated": 0,
                "requirementsEvaluated": 0,
                "status": "failed",
            },
            "results": []
        }, status=status.HTTP_200_OK)
    except Exception as e:
        msg = f"Unexpected error retrieving PatientExamination {pe_id}: {e}"
        errors.append(msg)
        logger.exception("evaluate_requirements: %s", msg)
        return Response({
            "ok": False,
            "errors": errors,
            "meta": {
                "patientExaminationId": pe_id,
                "setsEvaluated": 0,
                "requirementsEvaluated": 0,
                "status": "failed",
            },
            "results": []
        }, status=status.HTTP_200_OK)

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
        return Response({
            "ok": False,
            "errors": errors,
            "meta": {
                "patientExaminationId": pe_id,
                "setsEvaluated": 0,
                "requirementsEvaluated": 0,
                "status": "failed",
            },
            "results": []
        }, status=status.HTTP_200_OK)

    # ---- evaluate
    for req_set in requirement_sets:
        for req in req_set.requirements.all():
            # (optionally) reload Requirement to ensure fresh instance as in your original code
            try:
                requirement_obj = Requirement.objects.get(name=req.name)
            except Exception:
                # fall back to the prefetched instance
                requirement_obj = req

            try:
                met, details, error = safe_evaluate_requirement(requirement_obj, pe.patient, mode="strict")
                # normalize details to a string for the frontend
                if isinstance(details, str):
                    details_str = details
                else:
                    try:
                        details_str = json.dumps(details, ensure_ascii=False, default=str)
                    except Exception:
                        details_str = str(details)

                results.append({
                    "requirement_set_id": getattr(req_set, "id", None),
                    "requirement_set_name": getattr(req_set, "name", str(getattr(req_set, "id", ""))),
                    "requirement_name": getattr(requirement_obj, "name", "unknown"),
                    "met": bool(met),
                    "details": details_str if details_str else ("Voraussetzung erfüllt" if met else "Voraussetzung nicht erfüllt"),
                    "error": error
                })
            except (TypeError, ValueError) as e:
                msg = f"Fehler bei der Bewertung der Voraussetzung: {e}"
                logger.warning("evaluate_requirements: requirement '%s' error: %s",
                               getattr(requirement_obj, "name", "unknown"), e)
                results.append({
                    "requirement_set_id": getattr(req_set, "id", None),
                    "requirement_set_name": getattr(req_set, "name", str(getattr(req_set, "id", ""))),
                    "requirement_name": getattr(requirement_obj, "name", "unknown"),
                    "met": False,
                    "details": msg,
                    "error": f"{e.__class__.__name__}: {e}"
                })
                errors.append(msg)
            except Exception as e:
                msg = f"Unerwarteter Fehler bei der Bewertung: {e}"
                logger.exception("evaluate_requirements: requirement '%s' unexpected error",
                                 getattr(requirement_obj, "name", "unknown"))
                results.append({
                    "requirement_set_id": getattr(req_set, "id", None),
                    "requirement_set_name": getattr(req_set, "name", str(getattr(req_set, "id", ""))),
                    "requirement_name": getattr(requirement_obj, "name", "unknown"),
                    "met": False,
                    "details": msg,
                    "error": f"{e.__class__.__name__}: {e}"
                })
                errors.append(msg)

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
        "errors": errors,  # frontend can render these in a toast / banner
        "meta": {
            "patientExaminationId": pe_id,
            "setsEvaluated": sets_evaluated,
            "requirementsEvaluated": requirements_evaluated,
            "status": status_label
        },
        "results": results
    }

    return Response(response_payload, status=status.HTTP_200_OK)


def evaluate_requirement_set(request) -> Response:
    """
    Evaluate specific RequirementSets for a given PatientExamination.
    """
    try:
        data = request.data
        requirement_set_ids = data.get("requirement_set_ids")
        patient_examination_id = data.get("patient_examination_id")

        if not requirement_set_ids:
            return Response(
                {"error": "requirement_set_ids is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not patient_examination_id:
            return Response(
                {"error": "patient_examination_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            patient_examination = PatientExamination.objects.select_related("patient").get(
                id=patient_examination_id
            )
        except PatientExamination.DoesNotExist:
            return Response(
                {
                    "error": f"PatientExamination with id {patient_examination_id} does not exist"
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        patient = patient_examination.patient
        results = []

        for requirement_set_id in requirement_set_ids:
            try:
                req_set = RequirementSet.objects.prefetch_related("requirements").get(
                    id=requirement_set_id
                )
            except RequirementSet.DoesNotExist:
                return Response(
                    {"error": f"RequirementSet with id {requirement_set_id} does not exist"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            for requirement in req_set.requirements.all():
                met, details, error = safe_evaluate_requirement(
                    requirement, patient, mode="strict"
                )

                results.append(
                    {
                        "requirement_set_id": req_set.id,
                        "requirement_set_name": req_set.name,
                        "requirement_name": requirement.name,
                        "met": met,
                        "details": details,
                        "error": error,
                    }
                )

        return Response({"results": results}, status=status.HTTP_200_OK)

    except Exception as e:
        logger.exception("evaluate_requirement_set: unexpected error")
        return Response(
            {
                "results": [],
                "error": str(e),
                "details": "Fehler bei der Bewertung der Voraussetzung",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
from __future__ import annotations

import logging

from lx_dtypes.models.contracts import (
    RequirementEvaluationMeta,
    RequirementEvaluationRequest,
    RequirementEvaluationResponse,
    RequirementEvaluationResult,
    ValidationError,
)
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.services import lookup_service

logger = logging.getLogger(__name__)


def _format_validation_errors(exc: ValidationError) -> list[str]:
    errors: list[str] = []
    for error in exc.errors(include_url=False):
        loc = error.get("loc") or ()
        field_name = ".".join(str(part) for part in loc)
        message = str(error.get("msg") or "Invalid request payload")
        error_type = str(error.get("type") or "")

        if error_type == "missing" and field_name:
            errors.append(f"{field_name} is required")
            continue

        if field_name:
            errors.append(f"{field_name}: {message}")
        else:
            errors.append(message)
    return errors or ["Invalid request payload"]


def _build_response(
    *,
    ok: bool,
    errors: list[str],
    patient_examination_id: int | None,
    sets_evaluated: int,
    requirements_evaluated: int,
    status_label: str,
    results: list[RequirementEvaluationResult],
) -> Response:
    response_payload = RequirementEvaluationResponse(
        ok=ok,
        errors=errors,
        meta=RequirementEvaluationMeta(
            patient_examination_id=patient_examination_id,
            sets_evaluated=sets_evaluated,
            requirements_evaluated=requirements_evaluated,
            status=status_label,
        ),
        results=results,
    )
    return Response(
        response_payload.model_dump(mode="python"),
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def evaluate_requirements(request):
    """
    Evaluate requirement guidance using the lookup service dtypes runtime.

    Response contract is intentionally stable at top-level:
    - ok: bool
    - errors: list[str]
    - meta: object
    - results: list[object]
    """
    payload = request.data or {}
    errors: list[str] = []
    results: list[RequirementEvaluationResult] = []

    patient_examination_id: int | None = None
    selected_requirement_set_ids: list[int] | None = None

    try:
        request_payload = RequirementEvaluationRequest.model_validate(payload)
        patient_examination_id = request_payload.patient_examination_id
        selected_requirement_set_ids = request_payload.requirement_set_ids
    except ValidationError as exc:
        errors.extend(_format_validation_errors(exc))

    if errors:
        return _build_response(
            ok=False,
            errors=errors,
            patient_examination_id=patient_examination_id,
            sets_evaluated=0,
            requirements_evaluated=0,
            status_label="failed",
            results=[],
        )

    try:
        pe = lookup_service.load_patient_exam_for_eval(patient_examination_id)
    except PatientExamination.DoesNotExist:
        return _build_response(
            ok=False,
            errors=[
                f"PatientExamination with id {patient_examination_id} does not exist"
            ],
            patient_examination_id=patient_examination_id,
            sets_evaluated=0,
            requirements_evaluated=0,
            status_label="failed",
            results=[],
        )
    except Exception as exc:
        logger.exception(
            "evaluate_requirements: failed loading patient examination %s",
            patient_examination_id,
        )
        return _build_response(
            ok=False,
            errors=[
                f"Unexpected error retrieving PatientExamination {patient_examination_id}: {exc}"
            ],
            patient_examination_id=patient_examination_id,
            sets_evaluated=0,
            requirements_evaluated=0,
            status_label="failed",
            results=[],
        )

    try:
        guidance = lookup_service.evaluate_patient_exam_requirement_guidance(
            pe,
            selected_requirement_set_ids=selected_requirement_set_ids,
        )
    except Exception as exc:
        logger.exception(
            "evaluate_requirements: dtypes requirement guidance failed for pe=%s",
            patient_examination_id,
        )
        return _build_response(
            ok=False,
            errors=[f"Requirement evaluation failed: {exc}"],
            patient_examination_id=patient_examination_id,
            sets_evaluated=0,
            requirements_evaluated=0,
            status_label="failed",
            results=[],
        )

    requirements_by_set = guidance.get("requirements_by_set") or {}
    requirement_status = guidance.get("requirement_status") or {}
    suggested_actions = guidance.get("suggested_actions") or {}

    selected_set_filter = set(selected_requirement_set_ids or [])
    seen_set_ids: set[int] = set()
    for set_id_raw, set_requirements in requirements_by_set.items():
        try:
            set_id = int(set_id_raw)
        except (TypeError, ValueError):
            continue
        if selected_set_filter and set_id not in selected_set_filter:
            continue
        seen_set_ids.add(set_id)

        if not isinstance(set_requirements, list):
            continue

        for req in set_requirements:
            if not isinstance(req, dict):
                continue

            try:
                requirement_id = int(req.get("id"))
            except (TypeError, ValueError):
                continue
            requirement_key = str(requirement_id)
            requirement_name = str(req.get("name") or f"#{requirement_id}")
            met = bool(requirement_status.get(requirement_key, False))

            detail = "Voraussetzung erfüllt" if met else "Voraussetzung nicht erfüllt"
            if not met:
                actions = suggested_actions.get(requirement_key) or []
                if actions and isinstance(actions[0], dict):
                    note = actions[0].get("note")
                    if note:
                        detail = str(note)

            results.append(
                RequirementEvaluationResult(
                    requirement_set_id=set_id,
                    requirement_set_name=str(set_id),
                    requirement_name=requirement_name,
                    met=met,
                    details=detail,
                    error=None,
                    status="PASSED" if met else "FAILED",
                )
            )

    sets_evaluated = len(seen_set_ids)
    if selected_requirement_set_ids and sets_evaluated == 0:
        errors.append(
            f"No RequirementSets found for IDs: {selected_requirement_set_ids}"
        )

    if errors and results:
        status_label = "partial"
    elif errors:
        status_label = "failed"
    else:
        status_label = "ok"

    return _build_response(
        ok=not errors,
        errors=errors,
        patient_examination_id=patient_examination_id,
        sets_evaluated=sets_evaluated,
        requirements_evaluated=len(results),
        status_label=status_label,
        results=results,
    )

from collections.abc import Iterable

from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.finding.finding_intervention import FindingIntervention


def _int_pk(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError(f"Expected integer primary key, got {type(value).__name__}.")


def _intervention_rows(
    interventions: Iterable[object],
) -> list[dict[str, int | str]]:
    return [
        {
            "id": _int_pk(getattr(intervention, "pk")),
            "name": str(getattr(intervention, "name", "")),
        }
        for intervention in interventions
    ]


@api_view(["GET"])
def get_interventions_for_examination(request: HttpRequest, exam_id: int) -> Response:
    exam = get_object_or_404(Examination, id=exam_id)
    findings = exam.get_available_findings()
    interventions = (
        FindingIntervention.objects.filter(findings__in=findings)
        .distinct()
        .order_by("name", "id")
    )
    return Response(_intervention_rows(interventions))


@api_view(["GET"])
def get_interventions_for_finding(
    request: HttpRequest, exam_id: int, finding_id: int
) -> Response:
    exam = get_object_or_404(
        Examination.objects.prefetch_related("findings"), id=exam_id
    )
    finding = (
        exam.findings.prefetch_related("finding_interventions")
        .filter(id=finding_id)
        .first()
    )
    if finding is None:
        return Response(
            {"error": "Finding not found for this examination"},
            status=status.HTTP_404_NOT_FOUND,
        )

    interventions = finding.finding_interventions.all().order_by("name", "id")
    return Response(_intervention_rows(interventions))

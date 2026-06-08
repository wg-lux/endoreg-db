from typing import TypedDict

from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.examination.examination_indication import (
    ExaminationIndication,
)


class _IndicationChoiceRow(TypedDict):
    id: int
    name: str
    classification_ids: list[int]


def _int_pk(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError(f"Expected integer primary key, got {type(value).__name__}.")


@api_view(["GET"])
def get_indications_for_examination(request: HttpRequest, exam_id: int) -> Response:
    """
    Retrieve indication options for a given examination.

    Returns:
        list[dict]: [{"id": int, "name": str, "description": str}, ...]
    """
    exam = get_object_or_404(Examination, id=exam_id)
    indications = exam.indications.all().order_by("name", "id")
    payload = [
        {
            "id": _int_pk(getattr(indication, "pk")),
            "name": str(getattr(indication, "name", "")),
            "description": str(getattr(indication, "description", "") or ""),
        }
        for indication in indications
    ]
    return Response(payload)


@api_view(["GET"])
def get_indication_choices(request: HttpRequest, indication_id: int) -> Response:
    """
    Retrieve all possible classification choices for a specific indication.

    The response is de-duplicated by choice id and includes all owning
    classification ids so clients can keep dependent dropdown logic deterministic.

    Returns:
        list[dict]: [{"id": int, "name": str, "classification_ids": list[int]}, ...]
    """
    indication = get_object_or_404(
        ExaminationIndication.objects.prefetch_related("classifications__choices"),
        id=indication_id,
    )

    choices_by_id: dict[int, _IndicationChoiceRow] = {}
    for classification in indication.classifications.all():
        for choice in classification.choices.all():
            choice_id = _int_pk(getattr(choice, "pk"))
            row = choices_by_id.setdefault(
                choice_id,
                {
                    "id": choice_id,
                    "name": str(getattr(choice, "name", "")),
                    "classification_ids": [],
                },
            )
            row["classification_ids"].append(_int_pk(getattr(classification, "pk")))

    payload: list[dict[str, object]] = []
    for row in sorted(
        choices_by_id.values(), key=lambda item: (str(item["name"]), int(item["id"]))
    ):
        classification_ids = sorted(set(row["classification_ids"]))
        payload.append(
            {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "classification_ids": classification_ids,
            }
        )

    return Response(payload)

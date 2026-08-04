from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.examination.examination_indication import (
    ExaminationIndication,
)


@api_view(["GET"])
def get_indications_for_examination(request, exam_id):
    """
    Retrieve indication options for a given examination.

    Returns:
        list[dict]: [{"id": int, "name": str, "description": str}, ...]
    """
    exam = get_object_or_404(Examination, id=exam_id)
    indications = exam.indications.all().order_by("name", "id")
    payload = [
        {
            "id": indication.id,
            "name": indication.name,
            "description": indication.description or "",
        }
        for indication in indications
    ]
    return Response(payload)


@api_view(["GET"])
def get_indication_choices(request, indication_id):
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

    choices_by_id: dict[int, dict[str, object]] = {}
    for classification in indication.classifications.all():
        for choice in classification.choices.all():
            row = choices_by_id.setdefault(
                choice.id,
                {
                    "id": choice.id,
                    "name": choice.name,
                    "classification_ids": [],
                },
            )
            row["classification_ids"].append(classification.id)

    payload: list[dict[str, object]] = []
    for row in sorted(
        choices_by_id.values(), key=lambda item: (str(item["name"]), int(item["id"]))
    ):
        classification_ids = sorted({int(v) for v in row["classification_ids"]})
        payload.append(
            {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "classification_ids": classification_ids,
            }
        )

    return Response(payload)

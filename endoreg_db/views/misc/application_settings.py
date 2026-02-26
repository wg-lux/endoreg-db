from __future__ import annotations

from typing import Any

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from endoreg_db.models import (
    Center,
    EndoscopyProcessor,
    ImageClassificationAnnotation,
    PatientExaminationReport,
)
from endoreg_db.utils.defaults.set_default_center import (
    get_application_defaults,
    get_application_settings,
    update_application_defaults,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission


def _settings_payload() -> dict[str, Any]:
    settings_obj = get_application_settings()
    snapshot = get_application_defaults()
    return {
        "id": settings_obj.pk,
        "center_id": snapshot.center_id,
        "center_name": snapshot.center_name,
        "processor_id": snapshot.processor_id,
        "processor_name": snapshot.processor_name,
        "annotator_name": snapshot.annotator_name,
        "report_template_name": snapshot.report_template_name,
        "updated_at": settings_obj.updated_at.isoformat()
        if settings_obj.updated_at
        else None,
    }


@api_view(["GET", "PATCH"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_detail(request):
    if request.method == "GET":
        return Response(_settings_payload(), status=status.HTTP_200_OK)

    data = request.data
    center_value = data.get("center_id", data.get("center_name"))
    processor_value = data.get("processor_id", data.get("processor_name"))
    annotator_name = data.get("annotator_name")
    report_template_name = data.get("report_template_name")

    errors: dict[str, str] = {}
    if "center_id" in data or "center_name" in data:
        if center_value not in (None, "", 0):
            center_exists = (
                Center.objects.filter(pk=center_value).exists()
                if isinstance(center_value, int)
                else Center.objects.filter(name=center_value).exists()
            )
            if not center_exists:
                errors["center"] = "Center not found."
            else:
                pass
        if center_value in ("", 0):
            center_value = None

    if "processor_id" in data or "processor_name" in data:
        if processor_value not in (None, "", 0):
            processor_exists = (
                EndoscopyProcessor.objects.filter(pk=processor_value).exists()
                if isinstance(processor_value, int)
                else EndoscopyProcessor.objects.filter(name=processor_value).exists()
            )
            if not processor_exists:
                errors["processor"] = "Processor not found."
        if processor_value in ("", 0):
            processor_value = None

    if annotator_name is not None and not isinstance(annotator_name, str):
        errors["annotator_name"] = "annotator_name must be a string."
    if report_template_name is not None and not isinstance(report_template_name, str):
        errors["report_template_name"] = "report_template_name must be a string."

    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    update_application_defaults(
        center=center_value if ("center_id" in data or "center_name" in data) else None,
        processor=processor_value
        if ("processor_id" in data or "processor_name" in data)
        else None,
        annotator_name=annotator_name,
        report_template_name=report_template_name,
    )
    return Response(_settings_payload(), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_centers_dropdown(request):
    centers = Center.objects.order_by("name").values("id", "name")
    return Response(list(centers), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_processors_dropdown(request):
    processors = EndoscopyProcessor.objects.order_by("name").values("id", "name")
    return Response(list(processors), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_annotators_dropdown(request):
    values = list(
        ImageClassificationAnnotation.objects.exclude(annotator__isnull=True)
        .exclude(annotator__exact="")
        .order_by("annotator")
        .values_list("annotator", flat=True)
        .distinct()
    )
    current_value = get_application_settings().annotator_name
    if current_value and current_value not in values:
        values.insert(0, current_value)
    return Response(
        [{"value": value, "label": value} for value in values],
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_report_templates_dropdown(request):
    values = list(
        PatientExaminationReport.objects.exclude(template_name__exact="")
        .order_by("template_name")
        .values_list("template_name", flat=True)
        .distinct()
    )
    current_value = get_application_settings().report_template_name
    if current_value and current_value not in values:
        values.insert(0, current_value)
    return Response(
        [{"value": value, "label": value} for value in values],
        status=status.HTTP_200_OK,
    )


__all__ = [
    "application_settings_detail",
    "application_settings_centers_dropdown",
    "application_settings_processors_dropdown",
    "application_settings_annotators_dropdown",
    "application_settings_report_templates_dropdown",
]

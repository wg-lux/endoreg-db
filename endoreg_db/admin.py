from __future__ import annotations

from collections.abc import Iterable
from typing import TypedDict, cast

from django.contrib import admin
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import path
from django.urls.resolvers import URLPattern

from endoreg_db.models import (
    ApplicationSettings,
    Patient,
    Examination,
    # PatientExamination,
    Finding,
    FindingClassification,
    FindingClassificationChoice,
    FindingIntervention,  #  Import Finding Interventions
    PatientFindingIntervention,
)


class FindingClassificationChoiceAdminJson(TypedDict):
    id: int
    name: str


type FindingClassificationChoiceDbRow = tuple[int, str]


@admin.register(Examination)
class ExaminationAdmin(admin.ModelAdmin[Examination]):
    list_display = ("id", "name")
    search_fields = ("name",)
    list_filter = ("name",)
    ordering = ("name",)


@admin.register(ApplicationSettings)
class ApplicationSettingsAdmin(admin.ModelAdmin[ApplicationSettings]):
    list_display = (
        "id",
        "center",
        "processor",
        "annotator_name",
        "report_template_name",
        "updated_at",
    )
    fields = (
        "center",
        "processor",
        "annotator_name",
        "report_template_name",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        if ApplicationSettings.objects.exists():
            return False
        return super().has_add_permission(request)


@admin.register(PatientFindingIntervention)
class PatientFindingInterventionAdmin(admin.ModelAdmin[PatientFindingIntervention]):
    change_list_template = "admin/patient_finding_intervention.html"

    def changelist_view(
        self,
        request: HttpRequest,
        extra_context: dict[str, str] | None = None,
    ) -> HttpResponse:
        """
        Overrides the admin changelist view to provide additional context data for the template, including all patients, examinations, findings, classifications, and interventions relevant to patient finding interventions.
        """
        admin_context = {
            "patients": Patient.objects.all(),
            "examinations": Examination.objects.all(),
            "findings": Finding.objects.all(),
            "locations": FindingClassification.objects.filter(
                classification_types__name__iexact="location"
            ),
            "location_choices": FindingClassificationChoice.objects.none(),
            "morphologies": FindingClassification.objects.filter(
                classification_types__name__iexact="morphology"
            ),
            "morphology_choices": FindingClassificationChoice.objects.none(),
            "finding_interventions": FindingIntervention.objects.all(),
        }
        return super().changelist_view(
            request,
            extra_context=cast(dict[str, str], admin_context),
        )

    def get_location_choices_json(self, request: HttpRequest) -> JsonResponse:
        """
        Handles AJAX requests to retrieve location classification choices as JSON.

        Expects a "location" parameter in the GET request and returns a list of matching FindingClassificationChoice objects with their IDs and names. Returns an error message with appropriate HTTP status if the parameter is missing or an exception occurs.
        """
        location_id = request.GET.get("location")
        if not location_id:
            return JsonResponse({"error": "Location ID is required"}, status=400)

        try:
            choice_rows = cast(
                Iterable[FindingClassificationChoiceDbRow],
                FindingClassificationChoice.objects.filter(
                    classifications__id=location_id,
                    classifications__classification_types__name__iexact="location",
                ).values_list("id", "name"),
            )
            choices: list[FindingClassificationChoiceAdminJson] = [
                {
                    "id": choice_id,
                    "name": choice_name,
                }
                for choice_id, choice_name in choice_rows
            ]
            if not choices:
                return JsonResponse([], safe=False)
            return JsonResponse(choices, safe=False)
        except Exception as exc:
            return JsonResponse({"error": str(exc)}, status=500)

    def get_urls(self) -> list[URLPattern]:
        """Register JSON endpoint inside Django Admin"""
        urls = super().get_urls()
        custom_urls = [
            path(
                "ajax/get-location-choices/",
                self.admin_site.admin_view(self.get_location_choices_json),
                name="ajax_get_location_choices",
            ),
        ]
        return custom_urls + urls

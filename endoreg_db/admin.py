from django.urls import path
from django.contrib import admin
from django.http import JsonResponse
from endoreg_db.models import (
    Patient,
    Examination,
    # PatientExamination,
    Finding,
    FindingClassification,
    FindingClassificationChoice,
    FindingIntervention,  #  Import Finding Interventions
    PatientFindingIntervention,
)
# from endoreg_db.forms.patient_finding_intervention_form import (
#     PatientFindingInterventionForm,
# )
from endoreg_db.forms.patient_form import PatientForm


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    form = PatientForm
    list_display = ("id", "first_name", "last_name", "dob", "center")
    search_fields = ("first_name", "last_name", "email", "phone")
    list_filter = ("dob", "center")
    ordering = ("last_name",)


@admin.register(Examination)
class ExaminationAdmin(admin.ModelAdmin):
    list_display = ("id", "name")
    search_fields = ("name", )
    list_filter = ("name",)
    ordering = ("name",)


class PatientFindingInterventionAdmin(admin.ModelAdmin):
    change_list_template = "admin/patient_finding_intervention.html"

    def changelist_view(self, request, extra_context=None):
        """
        Overrides the admin changelist view to provide additional context data for the template, including all patients, examinations, findings, classifications, and interventions relevant to patient finding interventions.
        """
        extra_context = {
            "patients": Patient.objects.all(),
            "examinations": Examination.objects.all(),
            "findings": Finding.objects.all(),
            "locations": FindingClassification.objects.filter(classification_types__name__iexact="location"),
            "location_choices": FindingClassificationChoice.objects.none(),
            "morphologies": FindingClassification.objects.filter(classification_types__name__iexact="morphology"),
            "morphology_choices": FindingClassificationChoice.objects.none(),
            "finding_interventions": FindingIntervention.objects.all(),
        }
        return super().changelist_view(request, extra_context=extra_context)

    def get_location_choices_json(self, request):
        """
        Handles AJAX requests to retrieve location classification choices as JSON.
        
        Expects a "location" parameter in the GET request and returns a list of matching FindingClassificationChoice objects with their IDs and names. Returns an error message with appropriate HTTP status if the parameter is missing or an exception occurs.
        """
        location_id = request.GET.get("location")
        if not location_id:
            return JsonResponse({"error": "Location ID is required"}, status=400)

        try:
            choices = list(
                FindingClassificationChoice.objects.filter(
                    classifications__id=location_id,
                    classifications__classification_types__name__iexact="location"
                ).values("id", "name")
            )
            if not choices:
                return JsonResponse([], safe=False)
            return JsonResponse(choices, safe=False)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)

    def get_urls(self):
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


admin.site.register(PatientFindingIntervention, PatientFindingInterventionAdmin)

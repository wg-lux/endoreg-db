from django.urls import path
from django.contrib import admin
from django.http import JsonResponse
from endoreg_db.models import (
    Patient,
    Examination,
    PatientExamination,
    Finding,
    FindingLocationClassification,
    FindingMorphologyClassification,
    FindingIntervention,  #  Import Finding Interventions
    PatientFindingIntervention,
    FindingLocationClassificationChoice,
    FindingMorphologyClassificationChoice,
)
from endoreg_db.forms.patient_finding_intervention_form import (
    PatientFindingInterventionForm,
)
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
    search_fields = ("name", "name_de", "name_en")
    list_filter = ("name",)
    ordering = ("name",)


class PatientFindingInterventionAdmin(admin.ModelAdmin):
    change_list_template = "admin/patient_finding_intervention.html"

    def changelist_view(self, request, extra_context=None):
        """Pass Patients, Examinations, Findings, Locations, Location Choices, Morphologies, Morphology Choices, and Finding Interventions to the template"""
        extra_context = {
            "patients": Patient.objects.all(),
            "examinations": Examination.objects.all(),
            "findings": Finding.objects.all(),
            "locations": FindingLocationClassification.objects.all(),
            "location_choices": FindingLocationClassificationChoice.objects.none(),
            "morphologies": FindingMorphologyClassification.objects.all(),
            "morphology_choices": FindingMorphologyClassificationChoice.objects.all(),
            "finding_interventions": FindingIntervention.objects.all(),  #  Pass Finding Interventions
        }
        return super().changelist_view(request, extra_context=extra_context)

    def get_location_choices_json(self, request):
        """Ensure Django Admin returns JSON response for Location Choices"""
        location_id = request.GET.get("location")
        if not location_id:
            return JsonResponse({"error": "Location ID is required"}, status=400)

        try:
            choices = list(
                FindingLocationClassificationChoice.objects.filter(
                    location_classifications__id=location_id
                ).values("id", "name")
            )  #  Convert QuerySet to List

            if not choices:
                return JsonResponse(
                    [], safe=False
                )  #  Return an empty list if no data found

            return JsonResponse(choices, safe=False)  #  Return valid JSON

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

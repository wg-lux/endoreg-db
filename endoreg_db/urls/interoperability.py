"""Read-only FHIR R4 REST API routes (base: ``fhir/r4/``)."""

from django.urls import path

from endoreg_db.views.fhir_r4_api import (
    FhirInstanceView,
    FhirMetadataView,
    FhirPatientEverythingView,
    FhirTypeView,
)

# FHIR resource types and ids contain only [A-Za-z0-9.-]; the converters keep
# other characters out of the views.
urlpatterns = [
    path("fhir/r4/metadata", FhirMetadataView.as_view(), name="fhir-r4-metadata"),
    path(
        "fhir/r4/Patient/<slug:resource_id>/$everything",
        FhirPatientEverythingView.as_view(),
        name="fhir-r4-patient-everything",
    ),
    path(
        "fhir/r4/<slug:resource_type>/<slug:resource_id>",
        FhirInstanceView.as_view(),
        name="fhir-r4-read",
    ),
    path(
        "fhir/r4/<slug:resource_type>",
        FhirTypeView.as_view(),
        name="fhir-r4-search",
    ),
]

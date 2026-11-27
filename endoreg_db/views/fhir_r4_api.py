"""HTTP boundary of the read-only FHIR R4 API.

Authorization matches the per-examination FHIR export: the policy permission of
the route plus the caller's center scope. Every error is an OperationOutcome.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from urllib.parse import urlencode

from django.db.models import QuerySet
from django.utils import timezone
from lx_dtypes.terminology.terminology_loader import get_terminology_service
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.openapi import OpenApiAPIView
from endoreg_db.schemas.fhir_r4 import FhirR4Model, dump_fhir_r4_resource
from endoreg_db.services.interoperability.fhir_r4_api import (
    FhirApiError,
    IssueCode,
    capability_statement,
    parse_clinical_type,
    parse_terminology_type,
    patient_everything,
    read_clinical_resource,
    read_terminology_resource,
    search_clinical_resources,
    search_terminology_resources,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils.structured_logging import emit_structured_event
from endoreg_db.views.access_control import filter_center_scoped_queryset

logger = logging.getLogger("endoreg_db.interoperability.fhir_api")

FHIR_JSON = "application/fhir+json"


class FhirJsonRenderer(JSONRenderer):
    media_type = FHIR_JSON
    format = "fhir-json"


def _scope(request: Request) -> QuerySet[PatientExamination]:
    return filter_center_scoped_queryset(
        queryset=PatientExamination.objects.select_related("patient", "examination"),
        user=request.user,
        center_field="patient__center_id",
    )


def _params(request: Request) -> dict[str, list[str]]:
    # DRF's own ``format`` override is not a FHIR parameter; FHIR uses _format.
    return {
        key: request.query_params.getlist(key)
        for key in request.query_params
        if key != "format"
    }


def _page_url(request: Request) -> Callable[[int], str]:
    def build(offset: int) -> str:
        query = _params(request)
        query["_offset"] = [str(offset)]
        return request.build_absolute_uri(
            f"{request.path}?{urlencode(query, doseq=True)}"
        )

    return build


def _ok(resource: FhirR4Model) -> Response:
    return Response(dump_fhir_r4_resource(resource), content_type=FHIR_JSON)


def _outcome(error: FhirApiError) -> Response:
    return Response(
        dump_fhir_r4_resource(error.outcome()),
        status=error.status,
        content_type=FHIR_JSON,
    )


def _handle(action: Callable[[], FhirR4Model], *, interaction: str) -> Response:
    try:
        return _ok(action())
    except FhirApiError as error:
        emit_structured_event(
            logger,
            "fhir.api_request_rejected",
            level=logging.INFO,
            interaction=interaction,
            status=error.status,
            issue_code=error.code,
        )
        return _outcome(error)


def _framework_issue_code(status: int) -> IssueCode:
    if status in (401, 403):
        return "forbidden"
    if status == 404:
        return "not-found"
    return "not-supported"


def _active_terminology_bundle() -> Mapping[str, object]:
    return get_terminology_service().export_fhir()


class _FhirApiView(OpenApiAPIView):
    permission_classes = [EnvironmentAwarePermission, PolicyPermission]
    renderer_classes = [FhirJsonRenderer, JSONRenderer]

    def handle_exception(self, exc: Exception) -> Response:
        """Answer framework-level rejections (auth, method) as OperationOutcome."""

        response = super().handle_exception(exc)
        response.data = dump_fhir_r4_resource(
            FhirApiError(
                response.status_code,
                _framework_issue_code(response.status_code),
                "Request rejected by the server.",
            ).outcome()
        )
        response.content_type = FHIR_JSON
        return response


class FhirMetadataView(_FhirApiView):
    def get(self, request: Request) -> Response:
        return _handle(
            lambda: capability_statement(published=timezone.localdate()),
            interaction="capabilities",
        )


class FhirTypeView(_FhirApiView):
    def get(self, request: Request, resource_type: str) -> Response:
        def search() -> FhirR4Model:
            terminology_type = parse_terminology_type(resource_type)
            if terminology_type is not None:
                return search_terminology_resources(
                    _active_terminology_bundle,
                    terminology_type,
                    _params(request),
                    self_url=request.build_absolute_uri(),
                    page_url=_page_url(request),
                )
            return search_clinical_resources(
                _scope(request),
                parse_clinical_type(resource_type),
                _params(request),
                self_url=request.build_absolute_uri(),
                page_url=_page_url(request),
            )

        return _handle(search, interaction="search-type")


class FhirInstanceView(_FhirApiView):
    def get(self, request: Request, resource_type: str, resource_id: str) -> Response:
        def read() -> FhirR4Model:
            _reject_parameters(request)
            terminology_type = parse_terminology_type(resource_type)
            if terminology_type is not None:
                return read_terminology_resource(
                    _active_terminology_bundle, terminology_type, resource_id
                )
            return read_clinical_resource(
                _scope(request), parse_clinical_type(resource_type), resource_id
            )

        return _handle(read, interaction="read")


class FhirPatientEverythingView(_FhirApiView):
    def get(self, request: Request, resource_id: str) -> Response:
        def everything() -> FhirR4Model:
            _reject_parameters(request)
            return patient_everything(
                _scope(request),
                resource_id,
                self_url=request.build_absolute_uri(),
            )

        return _handle(everything, interaction="patient-everything")


def _reject_parameters(request: Request) -> None:
    unknown = sorted(set(_params(request)) - {"_format"})
    if unknown:
        raise FhirApiError(
            400, "not-supported", f"Unsupported parameter(s): {', '.join(unknown)}."
        )
    for value in _params(request).get("_format", []):
        if value not in {"json", FHIR_JSON, "application/json"}:
            raise FhirApiError(
                400, "not-supported", "Only the FHIR JSON format is supported."
            )


__all__ = [
    "FhirInstanceView",
    "FhirMetadataView",
    "FhirPatientEverythingView",
    "FhirTypeView",
]

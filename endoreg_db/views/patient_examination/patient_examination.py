from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Protocol, TypeAlias, cast

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from lx_dtypes.models.contracts.json_types import JsonObject
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.exceptions import FhirExportError
from endoreg_db.schemas.fhir_r4 import dump_fhir_r4_bundle
from endoreg_db.services.interoperability.fhir_r4 import (
    build_patient_examination_fhir_bundle,
)
from endoreg_db.serializers.patient.patient_dropdown import PatientDropdownSerializer
from endoreg_db.serializers.patient_examination import (
    PatientExaminationDraftResponseSerializer,
    PatientExaminationDraftSerializer,
    PatientExaminationSerializer,
)
from endoreg_db.serializers.examination import ExaminationDropdownSerializer
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.views.access_control import assert_center_scope_allowed
from endoreg_db.views.interoperability_errors import interoperability_error_response

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
RouteKwarg: TypeAlias = str | int | bool


class _SerializerDataLike(Protocol):
    @property
    def data(self) -> JsonValue: ...


class _SerializerErrorsLike(Protocol):
    @property
    def errors(self) -> JsonValue: ...


def _query_params(request: Request) -> Mapping[str, str]:
    return cast(Mapping[str, str], request.query_params)


def _serializer_data(serializer: _SerializerDataLike) -> JsonValue:
    return serializer.data


def _serializer_errors(serializer: _SerializerErrorsLike) -> JsonValue:
    return serializer.errors


def _report_draft_revision(draft: object) -> int:
    if not isinstance(draft, Mapping):
        raise ValueError("Persisted report draft must be an object.")
    revision = cast(Mapping[str, object], draft).get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError(
            "Persisted report draft revision must be a non-negative integer."
        )
    return revision


class PatientExaminationViewSet(viewsets.ModelViewSet[PatientExamination]):  # pyright: ignore[reportInvalidTypeArguments]
    """
    ViewSet für PatientExamination mit vollständiger CRUD-Unterstützung
    """

    queryset = PatientExamination.objects.all().select_related("patient", "examination")
    serializer_class = PatientExaminationSerializer

    def get_queryset(self) -> QuerySet[PatientExamination]:
        """Optimierte Abfrage mit besserer Performance"""
        return (
            PatientExamination.objects.select_related("patient", "examination")
            .prefetch_related("patient_findings", "indications")
            .order_by("-date_start", "-id")
        )

    def get_patient_examination_ids(self) -> list[int]:
        """Hilfsmethode zum Abrufen meherer PatientExamination IDs"""
        return list(PatientExamination.objects.values_list("id", flat=True))

    def get_patient_examination_by_id(
        self,
        pk: int | str,
    ) -> PatientExamination | None:
        """Hilfsmethode zum Abrufen einer PatientExamination nach ID"""
        if not PatientExamination.objects.filter(pk=pk).exists():
            return None
        else:
            return PatientExamination.objects.select_related(
                "patient", "examination"
            ).get(pk=pk)

    @action(detail=False, methods=["get"])
    def patients_dropdown(self, request: Request) -> Response:
        """
        Endpoint für Patient-Dropdown-Daten
        GET /api/patient-examinations/patients_dropdown/
        """
        patients = Patient.objects.all().order_by("first_name", "last_name")
        serializer = PatientDropdownSerializer(patients, many=True)
        return Response(_serializer_data(serializer))

    @action(detail=False, methods=["get"])
    def examinations_dropdown(self, request: Request) -> Response:
        """
        Endpoint für Examination-Dropdown-Daten
        GET /api/patient-examinations/examinations_dropdown/
        """
        examinations = Examination.objects.all().order_by("name")
        serializer = ExaminationDropdownSerializer(examinations, many=True)
        return Response(_serializer_data(cast(_SerializerDataLike, serializer)))

    @action(detail=False, methods=["get"])
    def recent(self, request: Request) -> Response:
        """
        Endpoint für die letzten PatientExaminations
        GET /api/patient-examinations/recent/
        """
        limit = int(_query_params(request).get("limit", "10"))
        recent_examinations = self.get_queryset()[:limit]
        serializer = self.get_serializer(recent_examinations, many=True)
        return Response(_serializer_data(cast(_SerializerDataLike, serializer)))

    @action(detail=True, methods=["get"])
    def details(self, request: Request, pk: str = "") -> Response:
        """
        Detaillierte Informationen über eine PatientExamination
        GET /api/patient-examinations/{id}/details/
        """
        examination = self.get_object()
        date_start = cast(date | None, getattr(examination, "date_start"))
        data: dict[str, JsonValue] = {
            "examination": _serializer_data(
                cast(_SerializerDataLike, PatientExaminationSerializer(examination))
            ),
            "findings": examination.get_findings().count(),
            "indications": examination.get_indications().count(),
            "patient_age_at_examination": examination.get_patient_age_at_examination()
            if date_start is not None
            else None,
        }
        return Response(data)

    @action(
        detail=True,
        methods=["get"],
        url_path="fhir",
        permission_classes=[EnvironmentAwarePermission, PolicyPermission],
    )
    def fhir_bundle(self, request: Request, pk: str = "") -> Response:
        """Export one patient examination as a validated FHIR R4 Bundle."""

        examination = self.get_object()
        assert_center_scope_allowed(
            request=request,
            obj=examination,
            not_found_message="Patient examination not found",
        )
        try:
            bundle = build_patient_examination_fhir_bundle(examination)
        except FhirExportError as error:
            return interoperability_error_response(error)
        return Response(
            dump_fhir_r4_bundle(bundle),
            content_type="application/fhir+json",
        )

    @action(detail=True, methods=["get", "put"])
    def draft(self, request: Request, pk: str = "") -> Response:
        """
        Draft endpoint for transient report editor state.

        GET /api/patient-examinations/{id}/draft/
        PUT /api/patient-examinations/{id}/draft/
        """
        examination = self.get_object()

        if request.method == "GET":
            current_revision = _report_draft_revision(examination.report_draft or {})
            serializer = PatientExaminationDraftResponseSerializer(
                {
                    "patient_examination_id": cast(int, examination.pk),
                    "revision": current_revision,
                    "draft": examination.report_draft or {},
                    "updated_at": cast(
                        datetime | None,
                        getattr(examination, "draft_updated_at"),
                    ),
                }
            )
            return Response(_serializer_data(cast(_SerializerDataLike, serializer)))

        serializer = PatientExaminationDraftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        expected_revision = serializer.validated_expected_revision

        with transaction.atomic():
            locked_examination = PatientExamination.objects.select_for_update().get(
                pk=examination.pk
            )
            current_revision = _report_draft_revision(
                locked_examination.report_draft or {}
            )
            if expected_revision != current_revision:
                return Response(
                    {
                        "detail": "The report draft was modified by another writer.",
                        "current_revision": current_revision,
                        "updated_at": cast(
                            datetime | None,
                            getattr(locked_examination, "draft_updated_at"),
                        ),
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            next_revision = current_revision + 1
            next_draft = dict(serializer.validated_data)
            next_draft["revision"] = next_revision
            locked_examination.report_draft = cast(JsonObject, next_draft)
            locked_examination.draft_updated_at = timezone.now()
            locked_examination.save(update_fields=["report_draft", "draft_updated_at"])

        response_serializer = PatientExaminationDraftResponseSerializer(
            {
                "patient_examination_id": cast(int, locked_examination.pk),
                "revision": next_revision,
                "draft": locked_examination.report_draft,
                "updated_at": cast(
                    datetime | None,
                    getattr(locked_examination, "draft_updated_at"),
                ),
            }
        )
        return Response(
            _serializer_data(cast(_SerializerDataLike, response_serializer)),
            status=status.HTTP_200_OK,
        )

    def create(
        self,
        request: Request,
        *args: str,
        **kwargs: RouteKwarg,
    ) -> Response:
        """
        Überschreibt die create-Methode für bessere Fehlerbehandlung
        """
        serializer = cast(
            PatientExaminationSerializer, self.get_serializer(data=request.data)
        )
        if serializer.is_valid():
            try:
                self.perform_create(serializer)
                response_data = _serializer_data(cast(_SerializerDataLike, serializer))
                headers = self.get_success_headers(
                    cast(dict[str, JsonValue], response_data)
                )
                return Response(
                    response_data, status=status.HTTP_201_CREATED, headers=headers
                )
            except Exception as e:
                return Response(
                    {"error": f"Fehler beim Erstellen der Untersuchung: {str(e)}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        return Response(
            _serializer_errors(cast(_SerializerErrorsLike, serializer)),
            status=status.HTTP_400_BAD_REQUEST,
        )

    def update(
        self,
        request: Request,
        *args: str,
        **kwargs: RouteKwarg,
    ) -> Response:
        """
        Überschreibt die update-Methode für bessere Fehlerbehandlung
        """
        partial = kwargs.pop("partial", False) is True
        instance = self.get_object()
        serializer = cast(
            PatientExaminationSerializer,
            self.get_serializer(instance, data=request.data, partial=partial),
        )

        if serializer.is_valid():
            try:
                self.perform_update(serializer)
                return Response(_serializer_data(cast(_SerializerDataLike, serializer)))
            except Exception as e:
                return Response(
                    {"error": f"Fehler beim Aktualisieren der Untersuchung: {str(e)}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        return Response(
            _serializer_errors(cast(_SerializerErrorsLike, serializer)),
            status=status.HTTP_400_BAD_REQUEST,
        )

    def get_findings_for_examination(
        self,
        request: Request,
        pk: str = "",
    ) -> Response:
        """
        Endpoint to retrieve findings for a specific PatientExamination
        GET /api/patient-examinations/{pk}/findings/
        """
        examination = self.get_patient_examination_by_id(pk)
        if not examination:
            return Response(
                {"error": "PatientExamination nicht gefunden"},
                status=status.HTTP_404_NOT_FOUND,
            )

        findings = examination.get_findings()
        finding_data: list[dict[str, JsonValue]] = [
            {"id": cast(int | None, f.pk), "name": str(f)} for f in findings
        ]
        return Response(finding_data)

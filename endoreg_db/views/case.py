from __future__ import annotations

from typing import Protocol, TypeVar, cast

from django.db import models, transaction
from django.db.models import QuerySet
from pydantic import ValidationError as PydanticValidationError
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.administration.case.case import Case
from endoreg_db.schemas.case_documents import CaseDocumentAttachmentPayload
from endoreg_db.schemas.case_creation import CreateCaseWithExaminationPayload
from endoreg_db.serializers.case import CaseSerializer
from endoreg_db.serializers.patient_examination import PatientExaminationSerializer
from endoreg_db.services.cases import (
    CaseLifecycleError,
    close_case,
    persist_case_graph,
    reopen_case,
)
from endoreg_db.services.case_documents import (
    CaseDocumentConflict,
    CaseDocumentNotFound,
    attach_document_to_case,
)
from endoreg_db.utils.pydantic_drf import drf_validation_error_detail
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.views.access_control import (
    assert_center_scope_allowed,
    filter_center_scoped_queryset,
)

_ModelT = TypeVar("_ModelT", bound=models.Model)


class _SerializerDataLike(Protocol):
    @property
    def data(self) -> object: ...


class CaseViewSet(viewsets.ModelViewSet[Case]):
    serializer_class = CaseSerializer
    permission_classes = [EnvironmentAwarePermission, PolicyPermission]
    lookup_field = "case_id"

    def get_queryset(self) -> QuerySet[Case]:
        queryset = (
            Case.objects.select_related("patient", "patient__center")
            .prefetch_related(
                "patient_examinations__patient",
                "patient_examinations__examination",
                "patient_examinations__raw_pdf_files",
                "patient_examinations__video_files",
                "patient_examinations__reports",
                "patient_medications",
                "patient_medication_schedules",
                "patient_lab_samples",
                "patient_lab_values",
            )
            .order_by("-start_date", "-id")
        )
        queryset = filter_center_scoped_queryset(
            queryset=queryset,
            user=self.request.user,
            center_field="patient__center_id",
        )
        patient_id = self.request.query_params.get("patient_id")
        if patient_id:
            queryset = queryset.filter(patient_id=patient_id)
        patient_examination_id = self.request.query_params.get("patient_examination_id")
        if patient_examination_id:
            queryset = queryset.filter(patient_examinations__id=patient_examination_id)
        return queryset

    def _assert_patient_scope(
        self, serializer: serializers.BaseSerializer[_ModelT]
    ) -> None:
        patient = serializer.validated_data.get("patient")
        if patient is None and serializer.instance is not None:
            patient = getattr(serializer.instance, "patient", None)
        assert_center_scope_allowed(
            request=self.request,
            obj=patient,
            not_found_message="Patient not found",
        )

    def perform_create(self, serializer: serializers.BaseSerializer[_ModelT]) -> None:
        self._assert_patient_scope(serializer)
        serializer.save()

    def perform_update(self, serializer: serializers.BaseSerializer[_ModelT]) -> None:
        self._assert_patient_scope(serializer)
        serializer.save()

    @action(detail=True, methods=["post"])
    def close(self, request: Request, case_id: str | None = None) -> Response:
        """Close a case with an optional explicit leave date."""
        del case_id
        leave_date_value = request.data.get("leave_date")
        leave_date = (
            serializers.DateTimeField().run_validation(leave_date_value)
            if leave_date_value is not None
            else None
        )
        try:
            patient_case = close_case(
                instance=self.get_object(),
                end_date=leave_date,
            )
        except CaseLifecycleError as exc:
            raise serializers.ValidationError({"leave_date": str(exc)}) from exc
        return Response(
            cast(_SerializerDataLike, CaseSerializer(patient_case)).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"])
    def reopen(self, request: Request, case_id: str | None = None) -> Response:
        """Reopen a closed case."""
        del request, case_id
        patient_case = reopen_case(instance=self.get_object())
        return Response(
            cast(_SerializerDataLike, CaseSerializer(patient_case)).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="documents")
    def attach_document(self, request: Request, case_id: str | None = None) -> Response:
        """Attach an existing PDF or video to an examination in this case."""
        del case_id
        visible_case = self.get_object()
        try:
            payload = CaseDocumentAttachmentPayload.model_validate(request.data)
        except PydanticValidationError as exc:
            return Response(
                {
                    "code": "validation-error",
                    "errors": drf_validation_error_detail(exc),
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        try:
            patient_case = attach_document_to_case(
                case_pk=cast(int, visible_case.pk),
                payload=payload,
            )
        except CaseDocumentNotFound as exc:
            return Response(
                {"code": "not-found", "detail": str(exc)},
                status=status.HTTP_404_NOT_FOUND,
            )
        except CaseDocumentConflict as exc:
            return Response(
                {"code": "conflict", "detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )

        patient_case = self.get_queryset().get(pk=patient_case.pk)
        return Response(
            cast(
                _SerializerDataLike,
                CaseSerializer(patient_case, context={"request": request}),
            ).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="create-with-examination")
    def create_with_examination(self, request: Request) -> Response:
        """Atomically create a patient examination and its owning case."""
        try:
            payload = CreateCaseWithExaminationPayload.model_validate(request.data)
        except PydanticValidationError as exc:
            raise serializers.ValidationError(drf_validation_error_detail(exc)) from exc

        with transaction.atomic():
            examination_serializer = PatientExaminationSerializer(
                data=payload.patient_examination.model_dump(mode="json")
            )
            examination_serializer.is_valid(raise_exception=True)
            patient_examination = examination_serializer.save()
            assert_center_scope_allowed(
                request=request,
                obj=patient_examination.patient,
                not_found_message="Patient not found",
            )
            patient_case = persist_case_graph(
                instance=None,
                scalar_values={
                    "patient": patient_examination.patient,
                    "start_date": payload.admission_date,
                },
                relationships={"patient_examinations": [patient_examination]},
            )
            response_data = {
                "case": cast(_SerializerDataLike, CaseSerializer(patient_case)).data,
                "patient_examination": cast(
                    _SerializerDataLike,
                    PatientExaminationSerializer(patient_examination),
                ).data,
            }

        return Response(
            response_data,
            status=status.HTTP_201_CREATED,
        )

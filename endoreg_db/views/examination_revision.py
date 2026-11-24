"""Patient-scoped CRUD for persisted examination alternatives."""

from django.shortcuts import get_object_or_404
from pydantic import ValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from lx_dtypes.models.contracts.examination_revision import (
    ExaminationRevisionCreate,
    ExaminationRevisionUpdate,
    ExaminationRevisionVersion,
)
from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.medical.patient.examination_revision import ExaminationRevision
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.openapi import OpenApiAPIView
from endoreg_db.services.cases.examination_revisions import (
    capture_examination_revision,
    delete_examination_revision,
    finalize_examination_revision,
    update_examination_revision,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.views.access_control import filter_center_scoped_queryset


def revision_response(record: ExaminationRevision) -> dict[str, object]:
    return {
        "id": record.pk,
        "patient_examination_id": record.patient_examination.pk,
        "title": record.title,
        "version": record.version,
        "snapshot": record.snapshot,
        "finalized_at": record.finalized_at.isoformat()
        if record.finalized_at
        else None,
        "updated_at": record.updated_at.isoformat(),
    }


class ExaminationRevisionViewSet(viewsets.ViewSet, OpenApiAPIView):
    permission_classes = [EnvironmentAwarePermission, PolicyPermission]
    lookup_value_regex = r"[1-9][0-9]*"

    def _examinations(self):
        return filter_center_scoped_queryset(
            queryset=PatientExamination.objects.select_related(
                "patient", "examination"
            ),
            user=self.request.user,
            center_field="patient__center",
        )

    def _record(self, pk: str | None) -> ExaminationRevision:
        return get_object_or_404(
            ExaminationRevision.objects.filter(
                patient_examination__in=self._examinations(),
            ).select_related("patient_examination"),
            pk=pk,
        )

    def list(self, request: Request) -> Response:
        identifier = request.query_params.get("patient_examination_id", "")
        if (
            not identifier.isascii()
            or not identifier.isdecimal()
            or int(identifier) < 1
        ):
            return Response(
                {"detail": "A positive patient_examination_id is required."}, status=422
            )
        examination = get_object_or_404(self._examinations(), pk=int(identifier))
        rows = ExaminationRevision.objects.filter(
            patient_examination=examination
        ).order_by("pk")
        return Response([revision_response(record) for record in rows])

    def retrieve(self, request: Request, pk: str | None = None) -> Response:
        return Response(revision_response(self._record(pk)))

    def create(self, request: Request) -> Response:
        try:
            payload = ExaminationRevisionCreate.model_validate(request.data)
            examination = get_object_or_404(
                self._examinations(), pk=payload.patient_examination_id
            )
            record = capture_examination_revision(examination, payload)
        except ValidationError:
            return Response(
                {"detail": "Invalid examination revision payload."}, status=422
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=409)
        return Response(revision_response(record), status=status.HTTP_201_CREATED)

    def update(self, request: Request, pk: str | None = None) -> Response:
        record = self._record(pk)
        try:
            payload = ExaminationRevisionUpdate.model_validate(request.data)
            record = update_examination_revision(record, payload)
        except ValidationError:
            return Response({"detail": "Invalid clinical content."}, status=422)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=409)
        return Response(revision_response(record))

    def destroy(self, request: Request, pk: str | None = None) -> Response:
        record = self._record(pk)
        try:
            payload = ExaminationRevisionVersion.model_validate(request.data)
            delete_examination_revision(record, payload.expected_version)
        except ValidationError:
            return Response({"detail": "expected_version is required."}, status=422)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=409)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def finalize(self, request: Request, pk: str | None = None) -> Response:
        record = self._record(pk)
        try:
            payload = ExaminationRevisionVersion.model_validate(request.data)
            record = finalize_examination_revision(record, payload.expected_version)
        except ValidationError:
            return Response({"detail": "expected_version is required."}, status=422)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=409)
        return Response(revision_response(record))

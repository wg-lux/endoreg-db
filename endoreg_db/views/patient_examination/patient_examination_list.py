from collections.abc import Mapping
from typing import cast

from django.db.models import QuerySet
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.serializers.patient_examination.patient_examination import (
    PatientExaminationPageSerializer,
)
from endoreg_db.views.access_control import filter_center_scoped_queryset


def scoped_examinations(request: Request) -> QuerySet[PatientExamination]:
    return filter_center_scoped_queryset(
        queryset=PatientExamination.objects.select_related("patient", "examination"),
        user=request.user,
        center_field="patient__center_id",
    ).order_by("-date_start", "-id")


class PatientExaminationListView(APIView):
    """Paginated patient examinations within the authenticated user's centers."""

    serializer_class = PatientExaminationPageSerializer
    permission_classes = [IsAuthenticated, PolicyPermission]

    def get_queryset(self) -> QuerySet[PatientExamination]:
        queryset = scoped_examinations(self.request)
        params = cast(Mapping[str, str], self.request.query_params)
        patient_id = params.get("patient_id")
        if patient_id:
            if not patient_id.isascii() or not patient_id.isdecimal():
                raise ValidationError({"patient_id": "Expected a patient ID."})
            queryset = queryset.filter(patient_id=patient_id)
        examination_name = params.get("examination_name")
        if examination_name:
            queryset = queryset.filter(examination__name__icontains=examination_name)
        return queryset

    def get(self, request: Request) -> Response:
        queryset = self.get_queryset()
        params = cast(Mapping[str, str], request.query_params)
        try:
            limit = int(params.get("limit", "20"))
            offset = int(params.get("offset", "0"))
        except ValueError as exc:
            raise ValidationError("Invalid pagination.") from exc
        if min(limit, offset) < 0:
            raise ValidationError("Pagination must be nonnegative.")
        total = queryset.count()
        page: dict[str, object] = {
            "results": queryset[offset : offset + limit],
            "total_count": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        }
        return Response(self.serializer_class().to_representation(page))

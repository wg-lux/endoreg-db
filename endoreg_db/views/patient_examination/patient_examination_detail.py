from typing import Any

from django.db import transaction
from django.db.models import QuerySet
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.serializers.patient_examination.patient_examination import (
    PatientExaminationDetailSerializer,
    PatientExaminationPatchResponseSerializer,
)
from .patient_examination_list import scoped_examinations


class PatientExaminationDetailView(generics.RetrieveUpdateAPIView[PatientExamination]):
    serializer_class = PatientExaminationDetailSerializer
    permission_classes = [IsAuthenticated, PolicyPermission]

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return PatientExaminationPatchResponseSerializer
        return PatientExaminationDetailSerializer

    def get_queryset(self) -> QuerySet[PatientExamination]:
        return scoped_examinations(self.request)

    @transaction.atomic
    def patch(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        # Preserve the PATCH success and validation envelopes used by existing clients.
        try:
            return self.partial_update(request, *args, **kwargs)
        except ValidationError as exc:
            return Response(
                {"error": "Validation failed", "details": exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )

import logging
from collections.abc import Mapping
from typing import Protocol, TypeAlias, cast

from django.db.models import QuerySet
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.serializers.patient_examination import PatientExaminationSerializer

from rest_framework import generics, status
from rest_framework.request import Request
from rest_framework.response import Response
from endoreg_db.utils.web.permissions import DEBUG_PERMISSIONS

logger = logging.getLogger(__name__)

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class _SerializerDataLike(Protocol):
    @property
    def data(self) -> JsonValue: ...


def _query_params(request: Request) -> Mapping[str, str]:
    return cast(Mapping[str, str], request.query_params)


def _serializer_data(serializer: _SerializerDataLike) -> JsonValue:
    return serializer.data


class PatientExaminationListView(generics.ListAPIView[PatientExamination]):
    """
    List PatientExamination instances with filtering.
    GET /api/examinations/list/

    Query parameters:
    - patient_id: Filter by patient ID
    - examination_name: Filter by examination name
    - limit: Number of results (default 20)
    - offset: Pagination offset (default 0)
    """

    serializer_class = PatientExaminationSerializer
    permission_classes = DEBUG_PERMISSIONS

    def get_queryset(self) -> QuerySet[PatientExamination]:
        queryset: QuerySet[PatientExamination] = PatientExamination.objects.select_related(
            "patient", "examination"
        ).order_by("-date_start", "-id")

        # Apply filters
        patient_id = _query_params(self.request).get("patient_id")
        if patient_id:
            queryset = queryset.filter(patient_id=patient_id)

        examination_name = _query_params(self.request).get("examination_name")
        if examination_name:
            queryset = queryset.filter(examination__name__icontains=examination_name)

        return queryset

    def list(self, request: Request, *args: str, **kwargs: str) -> Response:
        try:
            queryset = self.get_queryset()

            # Pagination
            query_params = _query_params(request)
            limit = int(query_params.get("limit", "20"))
            offset = int(query_params.get("offset", "0"))

            total_count = queryset.count()
            paginated_queryset = queryset[offset : offset + limit]

            serializer = self.get_serializer(paginated_queryset, many=True)

            return Response(
                {
                    "results": _serializer_data(cast(_SerializerDataLike, serializer)),
                    "total_count": total_count,
                    "limit": limit,
                    "offset": offset,
                    "has_more": (offset + limit) < total_count,
                }
            )

        except Exception as e:
            logger.error(f"Error listing examinations: {str(e)}")
            return Response(
                {"error": "Failed to list examinations"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

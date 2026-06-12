import logging
from collections.abc import Mapping
from typing import Protocol, TypeAlias, cast

from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.serializers.patient_examination import PatientExaminationSerializer

from django.db import transaction
from rest_framework import generics, status
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.utils.permissions import DEBUG_PERMISSIONS

logger = logging.getLogger(__name__)

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class _SerializerDataLike(Protocol):
    @property
    def data(self) -> Mapping[str, JsonValue]: ...


class _SerializerErrorsLike(Protocol):
    @property
    def errors(self) -> JsonValue: ...


def _serializer_data(serializer: _SerializerDataLike) -> Mapping[str, JsonValue]:
    return serializer.data


def _serializer_errors(serializer: _SerializerErrorsLike) -> JsonValue:
    return serializer.errors


class PatientExaminationDetailView(generics.RetrieveUpdateAPIView[PatientExamination]):
    """
    Retrieve and update PatientExamination instances.
    GET /api/examinations/{id}/
    PATCH /api/examinations/{id}/
    """

    queryset = PatientExamination.objects.select_related("patient", "examination")
    serializer_class = PatientExaminationSerializer
    permission_classes = DEBUG_PERMISSIONS

    def get(self, request: Request, *args: str, **kwargs: str) -> Response:
        try:
            instance = self.get_object()
            serializer = PatientExaminationSerializer(instance)
            return Response(_serializer_data(cast(_SerializerDataLike, serializer)))
        except Exception as e:
            logger.error(f"Error retrieving examination: {str(e)}")
            return Response(
                {"error": "Failed to retrieve examination"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @transaction.atomic
    def patch(self, request: Request, *args: str, **kwargs: str) -> Response:
        try:
            instance = self.get_object()
            serializer = PatientExaminationSerializer(
                instance, data=request.data, partial=True
            )

            if serializer.is_valid():
                serializer.save()

                response_data = dict(
                    _serializer_data(cast(_SerializerDataLike, serializer))
                )
                response_data["message"] = "Examination updated successfully"

                logger.info(f"Examination {instance.pk} updated successfully")
                return Response(response_data, status=status.HTTP_200_OK)
            else:
                return Response(
                    {
                        "error": "Validation failed",
                        "details": _serializer_errors(
                            cast(_SerializerErrorsLike, serializer)
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        except Exception as e:
            logger.error(f"Error updating examination: {str(e)}")
            return Response(
                {"error": "Failed to update examination", "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

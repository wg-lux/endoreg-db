import logging
from collections.abc import Mapping
from typing import Protocol, cast

from django.db import transaction
from lx_dtypes.models.contracts.json_types import JsonValue
from rest_framework import generics, status
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.serializers.patient_examination import PatientExaminationSerializer
from endoreg_db.utils.permissions import EnvironmentAwarePermission

logger = logging.getLogger(__name__)


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


class ExaminationCreateView(generics.CreateAPIView[PatientExamination]):  # pyright: ignore[reportInvalidTypeArguments]
    """
    Create new PatientExamination instances.
    POST /api/examinations/create/

    Expected payload:
    {
        "patient": "patient_hash_string",  # or patient_id integer
        "examination": "examination_name", # examination name string
        "date_start": "2024-01-15",
    }
    """

    serializer_class = PatientExaminationSerializer
    queryset = PatientExamination.objects.select_related("patient", "examination")
    permission_classes = [EnvironmentAwarePermission]

    @transaction.atomic
    def create(self, request: Request, *args: str, **kwargs: str) -> Response:
        try:
            logger.info(f"Creating examination with data: {request.data}")

            # Use the serializer for validation and creation
            serializer = cast(
                PatientExaminationSerializer, self.get_serializer(data=request.data)
            )

            if serializer.is_valid():
                # The serializer handles patient lookup/creation in validate_patient
                instance = serializer.save()

                response_data = dict(
                    _serializer_data(cast(_SerializerDataLike, serializer))
                )
                response_data["message"] = "Examination created successfully"

                logger.info(f"Examination created successfully with ID: {instance.pk}")
                return Response(response_data, status=status.HTTP_201_CREATED)
            else:
                validation_errors = _serializer_errors(
                    cast(_SerializerErrorsLike, serializer)
                )
                logger.warning(f"Validation errors: {validation_errors}")
                return Response(
                    {"error": "Validation failed", "details": validation_errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        except Exception as e:
            logger.error(f"Error creating examination: {str(e)}")
            return Response(
                {"error": "Failed to create examination", "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

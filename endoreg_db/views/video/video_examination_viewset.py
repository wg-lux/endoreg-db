# endoreg_db/views/video/video_examination_viewset.py
from __future__ import annotations

"""
Video Examination ViewSet

Provides REST API endpoints for managing video-based patient examinations.
Handles CRUD operations for PatientExamination records linked to VideoFile instances.

**API Endpoints:**
- GET /api/video-examinations/ - List all video examinations
- GET /api/video-examinations/{id}/ - Get examination details
- POST /api/video-examinations/ - Create new examination
- PATCH /api/video-examinations/{id}/ - Update examination
- DELETE /api/video-examinations/{id}/ - Delete examination
- GET /api/video/{video_id}/examinations/ - List examinations for specific video

**Frontend Integration:**
Used by VideoExaminationAnnotation.vue for annotation workflow.
"""

import logging
from collections.abc import Mapping
from typing import Any, Protocol, TypeAlias, cast

from django.db import transaction
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from lx_dtypes.models.contracts import (
    dump_video_examination_list_query_payload,
    validate_video_examination_list_query,
    validate_video_examination_path_payload,
)
from pydantic import ValidationError as PydanticValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.utils.pydantic_drf import (
    drf_validation_error_detail,
    drf_validation_error_response,
)

from ...serializers.video_examination import (
    VideoExaminationCreateSerializer,
    VideoExaminationSerializer,
    VideoExaminationUpdateSerializer,
)

logger = logging.getLogger(__name__)

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class _SerializerDataLike(Protocol):
    @property
    def data(self) -> JsonValue: ...


def _request_payload(request: Request) -> Mapping[str, Any]:
    payload = cast(object, request.data)
    if isinstance(payload, Mapping):
        return cast(Mapping[str, Any], payload)
    return {}


def _request_query(request: Request) -> Mapping[str, Any]:
    query_params = cast(object, request.query_params)
    query_dict = getattr(query_params, "dict", None)
    if callable(query_dict):
        return cast(dict[str, Any], query_dict())
    if isinstance(query_params, Mapping):
        return cast(Mapping[str, Any], query_params)
    return {}


class VideoExaminationViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Video Examination CRUD operations.

    Provides comprehensive API for managing patient examinations within
    the video annotation workflow. Supports filtering by video, patient,
    and examination type.

    **Usage Example:**
    ```python
    # Frontend (JavaScript)
    // Get examinations for video 123
    const response = await api.get('/api/video/123/examinations/');

    // Create new examination
    await api.post('/api/video-examinations/', {
        video_id: 123,
        examination_id: 5,
        date_start: '2024-01-15'
    });
    ```
    """

    queryset: QuerySet[PatientExamination] = PatientExamination.objects.select_related(
        "patient",
        "examination",
        "video",
    ).prefetch_related("patient_findings")

    serializer_class = VideoExaminationSerializer

    def get_serializer_class(self) -> type[BaseSerializer[PatientExamination]]:
        if self.action == "create":
            return VideoExaminationCreateSerializer
        if self.action in {"update", "partial_update"}:
            return VideoExaminationUpdateSerializer
        return VideoExaminationSerializer

    def get_queryset(self) -> QuerySet[PatientExamination]:
        """
        Filter examinations based on query parameters.

        **Supported filters:**
        - ?video_id=123 - Get examinations for specific video
        - ?patient_id=456 - Get examinations for specific patient
        - ?examination_id=789 - Get examinations of specific type
        """
        queryset: QuerySet[PatientExamination] = (
            PatientExamination.objects.select_related(
                "patient",
                "examination",
                "video",
            ).prefetch_related("patient_findings")
        )
        try:
            query_payload = validate_video_examination_list_query(
                _request_query(self.request)
            )
        except PydanticValidationError as exc:
            raise DRFValidationError(drf_validation_error_detail(exc)) from exc

        filters = dump_video_examination_list_query_payload(query_payload)

        if "video_id" in filters:
            queryset = queryset.filter(video_id=filters["video_id"])

        if "patient_id" in filters:
            queryset = queryset.filter(patient_id=filters["patient_id"])

        if "examination_id" in filters:
            queryset = queryset.filter(examination_id=filters["examination_id"])

        return queryset

    @action(detail=False, methods=["get"], url_path="video/(?P<video_id>[^/.]+)")
    def by_video(self, request: Request, video_id: str | None = None) -> Response:
        """
        Get all examinations for a specific video.

        **Endpoint:** GET /api/video-examinations/video/{video_id}/
        **Alternative:** GET /api/video/{video_id}/examinations/

        Args:
            video_id: ID of the video

        Returns:
            200: List of examinations for the video
            404: Video not found
        """
        try:
            path_payload = validate_video_examination_path_payload(
                {"video_id": video_id}
            )
        except PydanticValidationError as exc:
            return drf_validation_error_response(exc, message="Invalid video id")

        video = get_object_or_404(VideoFile, id=path_payload.video_id)

        # Get examinations for this video
        examinations = self.get_queryset().filter(video=video)

        serializer = self.get_serializer(examinations, many=True)
        return Response(serializer.data)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """
        Create a new video examination.

        **Endpoint:** POST /api/video-examinations/

        **Payload:**
        ```json
        {
            "video_id": 123,
            "examination_id": 5,
            "date_start": "2024-01-15",
            "date_end": "2024-01-15"
        }
        ```

        Returns:
            201: Examination created successfully
            400: Invalid data (missing required fields, validation errors)
            404: Video or examination type not found
        """
        request_payload = _request_payload(request)
        serializer = self.get_serializer(data=request_payload)
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                patient_exam = serializer.save()

                # Return created examination with full serialization
                response_serializer = cast(
                    BaseSerializer[PatientExamination],
                    VideoExaminationSerializer(patient_exam),
                )
                logger.info(
                    "Created video examination: video=%s, exam=%s",
                    request_payload.get("video_id"),
                    request_payload.get("examination_id"),
                )
                return Response(
                    response_serializer.data, status=status.HTTP_201_CREATED
                )
        except Exception as e:
            logger.error(f"Error creating video examination: {str(e)}")
            return Response(
                {"error": "Internal server error while creating examination"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        partial = bool(kwargs.pop("partial", False))
        instance = self.get_object()

        serializer = self.get_serializer(
            instance,
            data=_request_payload(request),
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                patient_exam = serializer.save()
                response_serializer = VideoExaminationSerializer(patient_exam)
                return Response(cast(_SerializerDataLike, response_serializer).data)
        except Exception as exc:
            logger.exception("Error updating video examination %s", instance.pk)
            return Response(
                {"error": f"Internal server error while updating examination {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """
        Delete a video examination.

        **Endpoint:** DELETE /api/video-examinations/{id}/

        Returns:
            204: Examination deleted successfully
            404: Examination not found
        """
        instance = self.get_object()
        examination_id = cast(int, instance.pk)

        try:
            with transaction.atomic():
                instance.delete()
                logger.info(f"Deleted video examination {examination_id}")
                return Response(
                    {"message": f"Examination {examination_id} deleted successfully"},
                    status=status.HTTP_204_NO_CONTENT,
                )
        except Exception as e:
            logger.error(f"Error deleting examination {examination_id}: {str(e)}")
            return Response(
                {"error": "Internal server error while deleting examination"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

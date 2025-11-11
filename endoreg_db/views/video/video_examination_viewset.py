# endoreg_db/views/video/video_examination_viewset.py
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

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from endoreg_db.models import PatientExamination, VideoFile

from ...serializers.video_examination import (
    VideoExaminationCreateSerializer,
    VideoExaminationSerializer,
    VideoExaminationUpdateSerializer,
)

logger = logging.getLogger(__name__)


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

    queryset = PatientExamination.objects.select_related(
        "patient", "examination", "video"
    ).prefetch_related("patient_findings")
    serializer_class = VideoExaminationSerializer

    def get_serializer_class(self):
        """
        Return appropriate serializer based on action.

        - create: VideoExaminationCreateSerializer (handles complex creation logic)
        - update/partial_update: VideoExaminationUpdateSerializer
        - list/retrieve: VideoExaminationSerializer (read-only with nested data)
        """
        if self.action == "create":
            return VideoExaminationCreateSerializer
        elif self.action in ["update", "partial_update"]:
            return VideoExaminationUpdateSerializer
        return VideoExaminationSerializer

    def get_queryset(self):
        """
        Filter examinations based on query parameters.

        **Supported filters:**
        - ?video_id=123 - Get examinations for specific video
        - ?patient_id=456 - Get examinations for specific patient
        - ?examination_id=789 - Get examinations of specific type
        """
        queryset = super().get_queryset()

        # Filter by video if provided
        video_id = self.request.query_params.get("video_id")
        if video_id:
            queryset = queryset.filter(video_id=video_id)

        # Filter by patient if provided
        patient_id = self.request.query_params.get("patient_id")
        if patient_id:
            queryset = queryset.filter(patient_id=patient_id)

        # Filter by examination type if provided
        examination_id = self.request.query_params.get("examination_id")
        if examination_id:
            queryset = queryset.filter(examination_id=examination_id)

        return queryset

    @action(detail=False, methods=["get"], url_path="video/(?P<video_id>[^/.]+)")
    def by_video(self, request, video_id=None):
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
        # Validate video exists
        video = get_object_or_404(VideoFile, id=video_id)

        # Get examinations for this video
        examinations = self.queryset.filter(video=video)

        serializer = self.get_serializer(examinations, many=True)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
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
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                patient_exam = serializer.save()

                # Return created examination with full serialization
                response_serializer = VideoExaminationSerializer(patient_exam)
                logger.info(
                    f"Created video examination: video={request.data.get('video_id')}, "
                    f"exam={request.data.get('examination_id')}"
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

    def update(self, request, *args, **kwargs):
        """
        Update an existing video examination.

        **Endpoint:** PATCH /api/video-examinations/{id}/

        **Payload:**
        ```json
        {
            "examination_id": 6,
            "date_start": "2024-01-16"
        }
        ```

        Returns:
            200: Examination updated successfully
            400: Invalid data
            404: Examination not found
        """
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                patient_exam = serializer.save()

                # Return updated examination
                response_serializer = VideoExaminationSerializer(patient_exam)
                logger.info(f"Updated video examination {instance.id}")
                return Response(response_serializer.data)
        except Exception as e:
            logger.error(f"Error updating video examination {instance.id}: {str(e)}")
            return Response(
                {"error": "Internal server error while updating examination"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def destroy(self, request, *args, **kwargs):
        """
        Delete a video examination.

        **Endpoint:** DELETE /api/video-examinations/{id}/

        Returns:
            204: Examination deleted successfully
            404: Examination not found
        """
        instance = self.get_object()
        examination_id = instance.id

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

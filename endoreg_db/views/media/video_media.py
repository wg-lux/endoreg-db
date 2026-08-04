"""
Video Media Management View (Phase 1.2)

Provides CRUD operations for video files including listing, detail retrieval,
and metadata management for the media management system.

This complements VideoStreamView which handles the actual video streaming.
"""

import logging

from django.db.models import Prefetch, Q
from django.http import Http404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.serializers.video.video_file_detail import VideoDetailSerializer
from endoreg_db.serializers.video.video_file_list import VideoFileListSerializer

from endoreg_db.authz.permissions import PolicyPermission

logger = logging.getLogger(__name__)


class VideoMediaView(APIView):
    """
    Video Media Management API for CRUD operations on video files.

    Endpoints:
    - GET /api/media/videos/ - List all videos with filtering
    - GET /api/media/videos/{id}/ - Get video details
    - PATCH /api/media/videos/{id}/ - Update video metadata (future)
    - DELETE /api/media/videos/{id}/ - Delete video

    Query Parameters:
    - status: Filter by processing status (not_started, processing, done, failed, validated)
    - search: Search in filename
    - limit: Limit results (default: 50)
    - offset: Pagination offset

    Examples:
    - GET /api/media/videos/?status=done&search=exam
    - GET /api/media/videos/123/

    Phase 1.2 Implementation:
    - List and detail views implemented
    - Filtering and search functionality
    - Pagination support
    - Error handling with proper HTTP status codes
    - Integration with existing serializers
    """

    permission_classes = [IsAuthenticated, PolicyPermission]

    def get(self, request, pk=None):
        """
        Handle GET requests for video listing or detail retrieval.

        Args:
            request: HTTP request object
            pk: Optional video ID for detail view

        Returns:
            Response: JSON response with video data or list

        Raises:
            Http404: If specific video not found
        """
        if pk is not None:
            # Detail view
            return self._get_video_detail(pk)
        else:
            # List view
            return self._list_videos(request)

    def patch(self, request, pk=None):
        if pk is None:
            return Response(
                {"error": "Video ID is required for update."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            video_id_int = int(pk)
        except (ValueError, TypeError):
            raise Http404("Invalid video ID format")

        try:
            video = VideoFile.objects.get(pk=video_id_int)
        except VideoFile.DoesNotExist:
            raise Http404(f"Video with ID {pk} not found")

        export_flag = request.data.get("export_segments_by_video")
        if export_flag is None:
            return Response(
                {"error": "No supported fields provided for update."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        video.export_segments_by_video = bool(export_flag)
        video.save(update_fields=["export_segments_by_video"])

        serializer = VideoDetailSerializer(video, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def _get_video_detail(self, pk):
        """
        Get detailed information for a specific video.

        Args:
            pk: Video primary key

        Returns:
            Response: JSON response with video details

        Raises:
            Http404: If video not found
        """
        try:
            # Validate video_id is numeric
            try:
                video_id_int = int(pk)
            except (ValueError, TypeError):
                raise Http404("Invalid video ID format")

            # Fetch video with related data
            video = VideoFile.objects.select_related("state", "sensitive_meta").get(
                pk=video_id_int
            )

            # Serialize with request context for URL generation
            serializer = VideoDetailSerializer(video, context={"request": self.request})

            return Response(serializer.data)

        except VideoFile.DoesNotExist:
            raise Http404(f"Video with ID {pk} not found")

        except Exception as e:
            logger.error(f"Unexpected error in video detail view for ID {pk}: {str(e)}")
            return Response(
                {"error": "Failed to retrieve video details"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _list_videos(self, request):
        """
        List videos with filtering, search, and pagination.

        Args:
            request: HTTP request with query parameters

        Returns:
            Response: JSON response with paginated video list
        """
        try:
            # Start with all videos
            segments_prefetch = Prefetch(
                "label_video_segments",
                queryset=LabelVideoSegment.objects.select_related(
                    "label", "video_file"
                ).order_by("start_frame_number"),
            )
            queryset = (
                VideoFile.objects.select_related("state", "sensitive_meta")
                .prefetch_related(segments_prefetch)
                .all()
            )

            # Apply filters
            queryset = self._apply_filters(queryset, request.query_params)

            # Apply search
            search = request.query_params.get("search", "").strip()
            if search:
                queryset = queryset.filter(Q(original_file_name__icontains=search))

            # Order by upload date (newest first)
            queryset = queryset.order_by("-uploaded_at")

            # Apply pagination
            limit = min(int(request.query_params.get("limit", 50)), 100)
            offset = int(request.query_params.get("offset", 0))

            total_count = queryset.count()
            videos = queryset[offset : offset + limit]

            # Serialize
            serializer = VideoFileListSerializer(videos, many=True)

            return Response(
                {
                    "count": total_count,
                    "next": self._get_next_url(request, offset, limit, total_count),
                    "previous": self._get_previous_url(request, offset, limit),
                    "results": serializer.data,
                }
            )

        except ValueError as e:
            return Response(
                {"error": f"Invalid query parameter: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception as e:
            logger.error(f"Unexpected error in video list view: {str(e)}")
            return Response(
                {"error": "Failed to retrieve video list"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _apply_filters(self, queryset, query_params):
        """
        Apply status and other filters to video queryset.

        Args:
            queryset: Base queryset to filter
            query_params: Request query parameters

        Returns:
            QuerySet: Filtered queryset
        """
        status_filter = query_params.get("status", "").strip().lower()
        failed_query = Q(state__processing_error=True) | Q(
            meta__integrity_status="lost"
        )

        if status_filter:
            if status_filter == "not_started":
                # Videos without state or with not_started status
                queryset = queryset.filter(
                    Q(state__isnull=True)
                    | Q(
                        state__frames_extracted=False,
                        state__sensitive_meta_processed=False,
                    )
                ).exclude(failed_query)
            elif status_filter == "processing":
                # Videos in any processing state
                queryset = queryset.filter(
                    state__frames_extracted=True, state__sensitive_meta_processed=False
                ).exclude(failed_query)
            elif status_filter == "done_processing_anonymization":
                # Videos with anonymization complete but not validated
                queryset = queryset.filter(
                    state__anonymized=True, sensitive_meta__is_verified=False
                ).exclude(failed_query)
            elif status_filter == "validated":
                # Videos with human validation complete
                queryset = queryset.filter(
                    state__anonymized=True, sensitive_meta__is_verified=True
                ).exclude(failed_query)
            elif status_filter == "failed":
                queryset = queryset.filter(failed_query)

        return queryset

    def _get_next_url(self, request, offset, limit, total_count):
        """Generate next page URL for pagination."""
        if offset + limit >= total_count:
            return None

        next_offset = offset + limit
        return self._build_paginated_url(request, next_offset, limit)

    def _get_previous_url(self, request, offset, limit):
        """Generate previous page URL for pagination."""
        if offset <= 0:
            return None

        prev_offset = max(0, offset - limit)
        return self._build_paginated_url(request, prev_offset, limit)

    def _build_paginated_url(self, request, offset, limit):
        """Build URL with pagination parameters."""
        params = request.query_params.copy()
        params["offset"] = offset
        params["limit"] = limit

        base_url = request.build_absolute_uri(request.path)
        if params:
            return f"{base_url}?{params.urlencode()}"
        return base_url

    # Future implementation placeholders
    def _patch_not_implemented_placeholder(self, request, pk):
        """
        Update video metadata (Phase 1.2+ future enhancement).

        Currently returns 501 Not Implemented.
        """
        return Response(
            {"error": "Video metadata updates not yet implemented"},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )

    def delete(self, request, pk):
        """
        Delete video file (Phase 1.2+ future enhancement).

        Currently returns 501 Not Implemented.
        Use /api/media-management/force-remove/{id}/ instead.
        """
        return Response(
            {
                "error": "Video deletion not yet implemented",
                "alternative": f"Use DELETE /api/media-management/force-remove/{pk}/ instead",
            },
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )

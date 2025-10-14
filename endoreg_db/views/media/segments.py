"""
Modern Media Framework - Video Segment API Views
October 14, 2025 - Migration to unified /api/media/videos/<pk>/segments/ pattern

This module provides modern framework views for video segment management,
wrapping legacy segment views with pk-based parameter handling.
"""
from endoreg_db.models import Label, LabelVideoSegment, VideoFile
from endoreg_db.serializers.label_video_segment.label_video_segment import LabelVideoSegmentSerializer

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from endoreg_db.utils.permissions import EnvironmentAwarePermission
import logging

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([EnvironmentAwarePermission])
def video_segments_by_pk(request, pk):
    """
    Modern media framework endpoint for retrieving video segments.
    
    GET /api/media/videos/<int:pk>/segments/?label=<label_name>
    
    Returns all segments for a video, optionally filtered by label name.
    This is the modern replacement for /api/video/<id>/segments/
    
    Query Parameters:
        label (str, optional): Filter segments by label name (e.g., 'outside')
    
    Returns:
        200: List of video segments
        404: Video not found
    """
    try:
        video = VideoFile.objects.get(id=pk)
    except VideoFile.DoesNotExist:
        logger.warning(f"Video with pk {pk} not found")
        return Response(
            {'error': f'Video with id {pk} not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Start with all segments for this video
    queryset = LabelVideoSegment.objects.filter(video_file=video)
    
    # Optional filtering by label name
    label_name = request.GET.get('label')
    if label_name:
        try:
            label = Label.objects.get(name=label_name)
            queryset = queryset.filter(label=label)
            logger.info(f"Filtering segments for video {pk} by label '{label_name}'")
        except Label.DoesNotExist:
            logger.warning(f"Label '{label_name}' not found, returning empty result")
            return Response(
                {'error': f"Label '{label_name}' not found"},
                status=status.HTTP_404_NOT_FOUND
            )
    
    # Order by start time for consistent results
    segments = queryset.order_by('start_frame_number')
    serializer = LabelVideoSegmentSerializer(segments, many=True)
    
    logger.info(f"Returning {len(segments)} segments for video {pk}")
    return Response(serializer.data)

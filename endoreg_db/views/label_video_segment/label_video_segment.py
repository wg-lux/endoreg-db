from endoreg_db.models import Label, LabelVideoSegment, VideoFile
from endoreg_db.serializers.label_video_segment.label_video_segment import LabelVideoSegmentSerializer

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from endoreg_db.utils.permissions import EnvironmentAwarePermission
import logging
logger = logging.getLogger(__name__)

@api_view(['POST', 'GET'])
@permission_classes([EnvironmentAwarePermission])
def video_segments_view(request):
    """
    Handles creation and retrieval of labeled video segments.

    POST creates a new labeled video segment with validated data and returns the created segment. GET lists all labeled video segments, optionally filtered by video ID and/or label ID. Returns appropriate error responses for invalid input or missing referenced objects.
    """
    if request.method == 'POST':
        logger.info(f"Creating new video segment with data: {request.data}")

        with transaction.atomic():
            serializer = LabelVideoSegmentSerializer(data=request.data)
            if serializer.is_valid():
                try:
                    segment = serializer.save()
                    logger.info(f"Successfully created video segment {segment.pk}")
                    return Response(
                        LabelVideoSegmentSerializer(segment).data,
                        status=status.HTTP_201_CREATED
                    )
                except Exception as e:
                    logger.error(f"Error creating video segment: {str(e)}")
                    return Response(
                        {'error': f'Failed to create segment: {str(e)}'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
            else:
                logger.warning(f"Invalid data for video segment creation: {serializer.errors}")
                return Response(
                    {'error': 'Invalid data', 'details': serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST
                )

    elif request.method == 'GET':
        # Optional filtering by video_id
        video_id = request.GET.get('video_id')
        label_id = request.GET.get('label_id')

        queryset = LabelVideoSegment.objects.all()

        if video_id:
            try:
                video = VideoFile.objects.get(id=video_id)
                queryset = queryset.filter(video_file=video)
            except VideoFile.DoesNotExist:
                return Response(
                    {'error': f'Video with id {video_id} not found'},
                    status=status.HTTP_404_NOT_FOUND
                )

        if label_id:
            try:
                label = Label.objects.get(id=label_id)
                queryset = queryset.filter(label=label)
            except Label.DoesNotExist:
                return Response(
                    {'error': f'Label with id {label_id} not found'},
                    status=status.HTTP_404_NOT_FOUND
                )

        # Order by video and start time for consistent results
        segments = queryset.order_by('video_file__id', 'start_frame_number')
        serializer = LabelVideoSegmentSerializer(segments, many=True)
        return Response(serializer.data)
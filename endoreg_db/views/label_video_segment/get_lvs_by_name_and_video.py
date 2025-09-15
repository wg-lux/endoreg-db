from endoreg_db.models import Label, LabelVideoSegment, VideoFile
from endoreg_db.serializers.label_video_segment.label_video_segment import LabelVideoSegmentSerializer

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from endoreg_db.utils.permissions import EnvironmentAwarePermission
import logging
logger = logging.getLogger(__name__)

@api_view(['GET'])
@permission_classes([EnvironmentAwarePermission])
def get_lvs_by_name_and_video_id(request, label_name, video_id):
    """
    Handles creation and retrieval of labeled video segments.
    GET lists all labeled video segments, filtered by video ID and label name.
    Returns appropriate error responses for invalid input or missing referenced objects.
    """

    # Check if label exists
    try:
        _label = Label.objects.get(name=label_name)
    except Label.DoesNotExist:
        logger.error(f"Label with name '{label_name}' does not exist.")
        return Response(
            {"error": f"Label with name '{label_name}' does not exist."},
            status=status.HTTP_404_NOT_FOUND
        )

    # Check if video exists
    try:
        _video = VideoFile.objects.get(id=video_id)
    except VideoFile.DoesNotExist:
        logger.error(f"Video with ID '{video_id}' does not exist.")
        return Response(
            {"error": f"Video with ID '{video_id}' does not exist."},
            status=status.HTTP_404_NOT_FOUND
        )

    # get all segments for the given video and label name
    queryset = LabelVideoSegment.objects.filter(
        video_file__id=video_id,
        label__name=label_name
    )

    # Order by video and start time for consistent results
    segments = queryset.order_by('video_file__id', 'start_frame_number')
    serializer = LabelVideoSegmentSerializer(segments, many=True)
    return Response(serializer.data)
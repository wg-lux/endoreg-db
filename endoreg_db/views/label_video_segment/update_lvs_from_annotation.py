from endoreg_db.models import LabelVideoSegment, InformationSource
from endoreg_db.serializers import LabelVideoSegmentSerializer
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
import logging

logger = logging.getLogger(__name__)

@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def update_label_video_segment(request, annotation_id) -> Response:
    """
    Update an existing LabelVideoSegment, treating it as a manual annotation.
    """
    logger.info(f"Updating LabelVideoSegment {annotation_id} with data: {request.data}")

    try:
        segment = LabelVideoSegment.objects.get(id=annotation_id)
    except LabelVideoSegment.DoesNotExist:
        logger.error(f"LabelVideoSegment {annotation_id} not found")
        return Response(
            {'error': 'Segment not found'},
            status=status.HTTP_404_NOT_FOUND
        )

    data = request.data
    updated = False

    if 'start_frame' in data:
        segment.start_frame_number = data['start_frame']
        updated = True
    
    if 'end_frame' in data:
        segment.end_frame_number = data['end_frame']
        updated = True

    if updated:
        # Ensure the source is correct for a manual annotation
        manual_source, _ = InformationSource.objects.get_or_create(name="manual_annotation")
        segment.source = manual_source
        segment.save()

    serializer = LabelVideoSegmentSerializer(segment)
    return Response(serializer.data)
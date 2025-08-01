from endoreg_db.models import LabelVideoSegment
from endoreg_db.serializers.label_video_segment.label_video_segment import LabelVideoSegmentSerializer
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from endoreg_db.utils.permissions import DEBUG_PERMISSIONS

import logging
logger = logging.getLogger(__name__)

@api_view(['GET', 'PUT', 'DELETE', 'PATCH'])
@permission_classes(DEBUG_PERMISSIONS)
def video_segment_detail_view(request, segment_id):
    """
    Handles retrieval, update, and deletion of a single labeled video segment.

    Supports:
    - GET: Returns details of the specified video segment.
    - PUT: Partially updates the segment with validated data.
    - DELETE: Removes the segment from the database.

    Returns appropriate HTTP status codes and error messages for invalid data or exceptions.
    """
    segment = get_object_or_404(LabelVideoSegment, id=segment_id)

    if request.method == 'GET':
        serializer = LabelVideoSegmentSerializer(segment)
        return Response(serializer.data)

    elif request.method in ('PUT', 'PATCH'):
        logger.info(f"Updating video segment {segment_id} with data: {request.data}")

        partial = request.method == 'PATCH'  # Allow partial updates with PATCH

        with transaction.atomic():
            serializer = LabelVideoSegmentSerializer(segment, data=request.data, partial=partial)
            if serializer.is_valid():
                try:
                    segment = serializer.save()
                    logger.info(f"Successfully updated video segment {segment_id}")
                    return Response(LabelVideoSegmentSerializer(segment).data)
                except Exception as e:
                    logger.error(f"Error updating video segment {segment_id}: {str(e)}")
                    return Response(
                        {'error': f'Failed to update segment: {str(e)}'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
            else:
                logger.warning(f"Invalid data for video segment update: {serializer.errors}")
                return Response(
                    {'error': 'Invalid data', 'details': serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST
                )

    elif request.method == 'DELETE':
        logger.info(f"Deleting video segment {segment_id}")
        try:
            with transaction.atomic():
                segment.delete()
                logger.info(f"Successfully deleted video segment {segment_id}")
                return Response(
                    {'message': f'Segment {segment_id} deleted successfully'},
                    status=status.HTTP_204_NO_CONTENT
                )
        except Exception as e:
            logger.error(f"Error deleting video segment {segment_id}: {str(e)}")
            return Response(
                {'error': f'Failed to delete segment: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
from endoreg_db.serializers.label_video_segment.label_video_segment_annotation import LabelVideoSegmentAnnotationSerializer
from endoreg_db.services.segment_sync import create_user_segment_from_annotation

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

import logging

logger = logging.getLogger(__name__)

@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def update_lvs_from_annotation(request, annotation_id):
    """
    Update an existing annotation.
    If annotation type is 'segment' and timing/label changed, creates a new user-source LabelVideoSegment.
    """
    logger.info(f"Updating annotation {annotation_id} with data: {request.data}")

    # For now, we'll simulate getting an annotation - in real implementation,
    # you'd retrieve from your annotation storage
    try:
        # This would be replaced with actual annotation retrieval
        # annotation = get_object_or_404(Annotation, id=annotation_id)

        with transaction.atomic():
            serializer = LabelVideoSegmentAnnotationSerializer(data=request.data, partial=True)
            if serializer.is_valid():
                try:
                    annotation = serializer.save()

                    # Check if this is a segment annotation and create user segment
                    if annotation.get('type') == 'segment':
                        new_segment = create_user_segment_from_annotation(annotation, request.user)
                        if new_segment:
                            # Update metadata with new segment ID
                            metadata = annotation.get('metadata', {})
                            metadata['segmentId'] = new_segment.id
                            annotation['metadata'] = metadata

                    logger.info(f"Successfully updated annotation {annotation_id}")
                    return Response(annotation, status=status.HTTP_200_OK)

                except Exception as e:
                    logger.error(f"Error updating annotation {annotation_id}: {str(e)}")
                    return Response(
                        {'error': f'Failed to update annotation: {str(e)}'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
            else:
                logger.warning(f"Invalid data for annotation update: {serializer.errors}")
                return Response(
                    {'error': 'Invalid data', 'details': serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST
                )

    except Exception as e:
        logger.error(f"Error retrieving annotation {annotation_id}: {str(e)}")
        return Response(
            {'error': f'Annotation not found: {str(e)}'},
            status=status.HTTP_404_NOT_FOUND
        )
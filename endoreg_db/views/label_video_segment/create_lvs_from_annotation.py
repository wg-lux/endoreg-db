from endoreg_db.serializers.label_video_segment.label_video_segment_annotation import LabelVideoSegmentAnnotationSerializer
from endoreg_db.services.segment_sync import create_user_segment_from_annotation

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

import logging

logger = logging.getLogger(__name__)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_video_segment_annotation(request):
    """
    Create a new annotation.
    If annotation type is 'segment', creates a user-source LabelVideoSegment.
    """
    logger.info(f"Creating annotation with data: {request.data}")

    with transaction.atomic():
        serializer = LabelVideoSegmentAnnotationSerializer(data=request.data)
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

                        # Update the annotation with new segment ID
                        serializer = LabelVideoSegmentAnnotationSerializer(instance=annotation, data={'metadata': metadata}, partial=True)
                        if serializer.is_valid():
                            annotation = serializer.save()

                logger.info(f"Successfully created annotation {annotation.get('id', 'unknown')}")
                return Response(annotation, status=status.HTTP_201_CREATED)

            except Exception as e:
                logger.error(f"Error creating annotation: {str(e)}")
                return Response(
                    {'error': f'Failed to create annotation: {str(e)}'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        else:
            logger.warning(f"Invalid data for annotation creation: {serializer.errors}")
            return Response(
                {'error': 'Invalid data', 'details': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )
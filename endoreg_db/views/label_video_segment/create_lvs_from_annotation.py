from endoreg_db.models import LabelVideoSegment
from endoreg_db.serializers.label_video_segment.label_video_segment_annotation import LabelVideoSegmentAnnotationSerializer

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
            annotation = serializer.save()

            # If the annotation type is 'segment', create a new LabelVideoSegment
            if annotation.type == 'segment':
                metadata = annotation.metadata or {}
                #TODO @coderabbitai we should create a followup issue for @Hamzaukw so we adress this naming inconsistency in future
                LabelVideoSegment.objects.create(
                    video_id=annotation.video_id,
                    start_frame=metadata.get('start_frame'),
                    end_frame=metadata.get('end_frame'),
                    label=metadata.get('label'),
                    source='user'
                )
            
            logger.info(f"Successfully created annotation {annotation.id}")
            return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    logger.error(f"Failed to create annotation with data: {request.data}, errors: {serializer.errors}")
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
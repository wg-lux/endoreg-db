from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db import transaction
import logging

from ..serializers.annotation_serializers import AnnotationSerializer
from ..services.segment_sync import create_user_segment_from_annotation

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_annotation(request):
    """
    Create a new annotation.
    If annotation type is 'segment', creates a user-source LabelVideoSegment.
    """
    logger.info(f"Creating annotation with data: {request.data}")
    
    with transaction.atomic():
        serializer = AnnotationSerializer(data=request.data)
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
                        serializer = AnnotationSerializer(instance=annotation, data={'metadata': metadata}, partial=True)
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


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def update_annotation(request, annotation_id):
    """
    Update an existing annotation.
    If annotation type is 'segment' and timing/label changed, creates a new user-source LabelVideoSegment.
    """
    logger.info(f"Updating annotation {annotation_id} with data: {request.data}")
    
    # For now, we'll simulate getting an annotation - in real implementation,
    # you'd retrieve from your annotation storage
    try:
        # This would be replaced with actual annotation retrieval
        #annotation = get_object_or_404(Annotation, id=annotation_id)
        
        with transaction.atomic():
            serializer = AnnotationSerializer(data=request.data, partial=True)
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
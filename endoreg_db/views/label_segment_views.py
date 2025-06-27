from re import DEBUG
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db import transaction
import logging

from ..models import VideoFile, LabelVideoSegment, Label
from ..serializers.label_serializer import LabelVideoSegmentSerializer
from ..utils.permissions import dynamic_permission_classes, DEBUG_PERMISSIONS

logger = logging.getLogger(__name__)

#TODO move DEFAULT_FPS to settings and fetch from environment with a sensible default
DEFAULT_FPS = 30  # Consistent default FPS for all video segment calculations

@api_view(['POST', 'GET'])
@permission_classes(DEBUG_PERMISSIONS)
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
                    logger.info(f"Successfully created video segment {segment.id}")
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

@api_view(['GET'])
@permission_classes(DEBUG_PERMISSIONS) #TODO: Uncomment this line if authentication is set up
def video_segments_by_label_id_view(request, video_id, label_id):
    """
    Retrieves all labeled segments for a given video and label by their IDs.
    
    Returns a list of time segments for the specified video and label, including start and end frames and their corresponding times in seconds. If no segments exist, returns an empty list. Responds with 404 if the video or label is not found.
    """
    try:
        video = get_object_or_404(VideoFile, id=video_id)
        label = get_object_or_404(Label, id=label_id)
        
        # Get all segments for this video and label combination
        segments = LabelVideoSegment.objects.filter(
            video_file=video,
            label=label
        ).order_by('start_frame_number')
        
        # Build response in the expected format
        if segments.exists():
            # Get video properties for time calculation
            fps = video.get_fps() or DEFAULT_FPS  # Use module-level constant
            
            time_segments = []
            for segment in segments:
                # Convert frame numbers to time
                start_time = segment.start_frame_number / fps
                end_time = segment.end_frame_number / fps
                
                time_segments.append({
                    'segment_start': segment.start_frame_number,
                    'segment_end': segment.end_frame_number,
                    'start_time': start_time,
                    'end_time': end_time,
                    'frames': {}  # Empty for now, can be populated if needed
                })
            
            response_data = {
                'label': label.name,
                'label_id': label.id,
                'time_segments': time_segments
            }
        else:
            # Return empty response if no segments found
            response_data = {
                'label': label.name,
                'label_id': label.id,
                'time_segments': []
            }
        
        return Response(response_data)
        
    except VideoFile.DoesNotExist:
        return Response(
            {'error': f'Video with id {video_id} not found'}, 
            status=status.HTTP_404_NOT_FOUND
        )
    except Label.DoesNotExist:
        return Response(
            {'error': f'Label with id {label_id} not found'}, 
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        logger.error(f"Error fetching segments for video {video_id} and label {label_id}: {str(e)}")
        return Response(
            {'error': f'Internal server error: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['GET'])
@permission_classes(DEBUG_PERMISSIONS) #TODO: Uncomment this line if authentication is set up
def video_segments_by_label_name_view(request, video_id, label_name):
    """
    Retrieves labeled video segments for a given video and label name.
    
    Handles cases where multiple labels share the same name by returning a conflict response with details of all matching labels and a suggestion to use the label ID endpoint. If a unique label is found, returns a list of segments for the specified video and label, including frame and time information. Returns appropriate error responses if the video or label is not found, or if an internal error occurs.
    """
    try:
        video = get_object_or_404(VideoFile, id=video_id)
        
        # Find all labels with this name
        labels_with_name = Label.objects.filter(name=label_name)
        
        if not labels_with_name.exists():
            return Response(
                {'error': f'No labels found with name "{label_name}"'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        if labels_with_name.count() > 1:
            # Multiple labels with same name - log warning and return info about all
            logger.warning(f"Multiple labels found with name '{label_name}': {[l.id for l in labels_with_name]}")
            
            return Response({
                'error': f'Multiple labels found with name "{label_name}"',
                'suggestion': 'Use the label-id endpoint instead for unambiguous results',
                'available_labels': [
                    {'id': label.id, 'name': label.name, 'description': label.description}
                    for label in labels_with_name
                ]
            }, status=status.HTTP_409_CONFLICT)
        
        # Single label found - proceed normally
        label = labels_with_name.first()
        
        # Get all segments for this video and label combination
        segments = LabelVideoSegment.objects.filter(
            video_file=video,
            label=label
        ).order_by('start_frame_number')
        
        # Build response in the expected format
        if segments.exists():
            # Get video properties for time calculation
            fps = video.get_fps() or DEFAULT_FPS  # Use module-level constant
            
            time_segments = []
            for segment in segments:
                # Convert frame numbers to time
                start_time = segment.start_frame_number / fps
                end_time = segment.end_frame_number / fps
                
                time_segments.append({
                    'segment_start': segment.start_frame_number,
                    'segment_end': segment.end_frame_number,
                    'start_time': start_time,
                    'end_time': end_time,
                    'frames': {}  # Empty for now, can be populated if needed
                })
            
            response_data = {
                'label': label.name,
                'label_id': label.id,
                'time_segments': time_segments
            }
        else:
            # Return empty response if no segments found
            response_data = {
                'label': label.name,
                'label_id': label.id,
                'time_segments': []
            }
        
        return Response(response_data)
        
    except VideoFile.DoesNotExist:
        return Response(
            {'error': f'Video with id {video_id} not found'}, 
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        logger.error(f"Error fetching segments for video {video_id} and label '{label_name}': {str(e)}")
        return Response(
            {'error': f'Internal server error: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
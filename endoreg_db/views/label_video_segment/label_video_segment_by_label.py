from endoreg_db.models import Label, LabelVideoSegment, VideoFile
from django.shortcuts import get_object_or_404
from django.http import Http404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from endoreg_db.utils.permissions import DEBUG_PERMISSIONS

import logging

logger = logging.getLogger(__name__)

DEFAULT_FPS = 50 #TODO move to settings or config

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
    except Http404 as e:
        return Response({'error': str(e)}, status=status.HTTP_404_NOT_FOUND)
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
    except Http404 as e:
        return Response({'error': str(e)}, status=status.HTTP_404_NOT_FOUND)
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
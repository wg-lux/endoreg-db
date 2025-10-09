from pathlib import Path
import os
import mimetypes
from django.http import FileResponse, Http404
from rest_framework import viewsets, decorators, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes

from ...serializers.label_video_segment.label_video_segment_update import LabelSegmentUpdateSerializer

from ...serializers.label.label import LabelSerializer

from ...serializers.video.video_file_list import VideoFileListSerializer
from ...models import VideoFile, Label, LabelVideoSegment
from ...serializers.video.segmentation import VideoFileSerializer
from ...utils.permissions import DEBUG_PERMISSIONS

# Phase 3.2: Import video streaming functionality from dedicated module
# Phase 3.2: VideoStreamView and _stream_video_file moved to video_stream.py
from .video_stream import _stream_video_file


class VideoViewSet(viewsets.ReadOnlyModelViewSet):
    """
    /api/videos/          → list of metadata   (JSON)
    /api/videos/<id>/     → single metadata   (JSON)
    /videos/<id>/stream/  → raw file          (FileResponse, range-aware)
    """
    queryset = VideoFile.objects.all()
    serializer_class = VideoFileListSerializer   # for the list view
    permission_classes = DEBUG_PERMISSIONS

    def list(self, request, *args, **kwargs):
        """
        Returns a JSON response with all video metadata and available labels.
        
        The response includes serialized lists of all videos and labels in the database.
        """
        videos = VideoFile.objects.all()
        labels = Label.objects.all()

        video_serializer = VideoFileListSerializer(videos, many=True)
        label_serializer = LabelSerializer(labels, many=True)

        return Response({
            "videos": video_serializer.data, 
            "labels": label_serializer.data  
        }, status=status.HTTP_200_OK)

    # ---------- JSON ---------- #
    def retrieve(self, request, *args, **kwargs):
        """
        Retrieves detailed metadata for a specific video as a JSON response.
        
        Returns:
            JSON representation of the requested video's metadata.
        """
        obj = self.get_object()
        return Response(VideoFileSerializer(obj, context={'request': request}).data)

    # ---------- BYTES ---------- #
    @decorators.action(methods=['get'], detail=True,
                       url_path='stream', renderer_classes=[])  # <- disable HTML & JSON renderers
    def stream(self, request, pk=None):
        """
        Streams the raw video file for the specified video with HTTP range and CORS support.
        """
        try:
            vf: VideoFile = self.get_object()
            frontend_origin = os.environ.get('FRONTEND_ORIGIN', 'http://localhost:8000')
            return _stream_video_file(vf, frontend_origin)
        except Http404:
            # Re-raise Http404 exceptions as they should bubble up
            raise
        except Exception as e:
            # Log unexpected errors and convert to Http404
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Unexpected error in video stream for pk={pk}: {str(e)}")
            raise Http404("Video streaming failed")


# Phase 3.2: VideoStreamView moved to video_stream.py - imported at top
# Old implementation removed to avoid duplication


class VideoLabelView(APIView):
    """
    API to fetch time segments (start & end times in seconds) for a specific label.
    """

    def get(self, request, video_id, label_name):
        """
        Retrieves time segments and frame-level predictions for a specific label on a video.
        
        Returns a JSON response containing the label name, a list of time segments (with frame ranges and timestamps), and frame-wise prediction data. Responds with HTTP 404 if the video or label does not exist, or HTTP 200 with empty data if no segments are found. Returns HTTP 500 with an error message for unexpected exceptions.
        """
        try:
            # Verify video exists
            video_entry = VideoFile.objects.get(id=video_id)
            
            # Try to get label by name
            try:
                label = Label.objects.get(name=label_name)
            except Label.DoesNotExist:
                return Response({
                    "error": f"Label '{label_name}' not found in database"
                }, status=status.HTTP_404_NOT_FOUND)
            
            # Get label video segments directly from the database
            label_segments = LabelVideoSegment.objects.filter(
                video_file=video_entry,
                label=label
            ).order_by('start_frame_number')
            
            if not label_segments.exists():
                # No segments found for this label, return empty data
                return Response({
                    "label": label_name,
                    "time_segments": [],
                    "frame_predictions": {}
                }, status=status.HTTP_200_OK)
            
            # Convert segments to time-based format
            # Fix: Ensure fps is a number, not a string
            fps_raw = getattr(video_entry, 'fps', None) or (video_entry.get_fps() if hasattr(video_entry, 'get_fps') else 25)
            
            # Convert fps to float if it's a string
            try:
                if isinstance(fps_raw, str):
                    fps = float(fps_raw)
                elif isinstance(fps_raw, (int, float)):
                    fps = float(fps_raw)
                else:
                    fps = 25.0  # Default fallback
            except (ValueError, TypeError):
                fps = 25.0  # Default fallback if conversion fails
            
            # Ensure fps is positive
            if fps <= 0:
                fps = 25.0
            
            time_segments = []
            frame_predictions = {}
            
            for segment in label_segments:
                # Now fps is guaranteed to be a float
                start_time = segment.start_frame_number / fps
                end_time = segment.end_frame_number / fps
                
                segment_data = {
                    "segment_id": segment.id,
                    "segment_start": segment.start_frame_number,
                    "segment_end": segment.end_frame_number,
                    "start_time": round(start_time, 2),
                    "end_time": round(end_time, 2),
                    "frames": {}
                }
                
                # Add frame-wise data if available
                for frame_num in range(segment.start_frame_number, segment.end_frame_number + 1):
                    frame_filename = f"frame_{str(frame_num).zfill(7)}.jpg"
                    frame_predictions[frame_num] = {
                        "frame_number": frame_num,
                        "label": label_name,
                        "confidence": 1.0  # Default confidence
                    }
                    
                    # Fix: Safely construct frame_file_path to avoid string/string division errors
                    frame_file_path = ""
                    if hasattr(video_entry, 'frame_dir') and video_entry.frame_dir:
                        try:
                            # Ensure frame_dir is converted to Path properly
                            if isinstance(video_entry.frame_dir, str):
                                frame_dir = Path(video_entry.frame_dir)
                            elif isinstance(video_entry.frame_dir, Path):
                                frame_dir = video_entry.frame_dir
                            else:
                                # Try to convert to string first, then to Path
                                frame_dir = Path(str(video_entry.frame_dir))
                            
                            frame_file_path = str(frame_dir / frame_filename)
                        except (TypeError, ValueError) as e:
                            # Log warning but don't fail the request
                            import logging
                            logger = logging.getLogger(__name__)
                            logger.warning(f"Could not construct frame path for frame {frame_num}: {e}")
                            frame_file_path = ""
                    
                    segment_data["frames"][frame_num] = {
                        "frame_filename": frame_filename,
                        "frame_file_path": frame_file_path,
                        "predictions": frame_predictions[frame_num]
                    }
                
                time_segments.append(segment_data)
            
            return Response({
                "label": label_name,
                "time_segments": time_segments,
                "frame_predictions": frame_predictions
            }, status=status.HTTP_200_OK)

        except VideoFile.DoesNotExist:
            return Response({
                "error": "Video not found"
            }, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            # Log the error for debugging
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in VideoLabelView for video {video_id}, label {label_name}: {str(e)}")
            
            return Response({
                "error": f"Internal error: {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class UpdateLabelSegmentsView(APIView):
    """
    API to update or create label segments for a video.
    """

    def put(self, request, video_id, label_id):
        """
        Updates segments for a given video & label.
        """

        # Prepare data for serializer by combining URL params with request data
        serializer_data = {
            "video_id": video_id,    # From URL parameter
            "label_id": label_id,    # From URL parameter  
            "segments": request.data.get("segments", [])  # From request body
        }

        # Validate input data
        serializer = LabelSegmentUpdateSerializer(data=serializer_data)

        if not serializer.is_valid():
            return Response({"error": "Invalid segment data", "details": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        # Process and save segment updates
        try:
            result = serializer.save()
            return Response({
                "message": "Segments updated successfully.",
                "updated_segments": result["updated_segments"],
                "new_segments": result["new_segments"],
                "deleted_segments": result["deleted_segments"]
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({
                "error": f"Failed to update segments: {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
           
@api_view(['GET'])
@permission_classes(DEBUG_PERMISSIONS)
def rerun_segmentation(request, video_id):
    """
    Rerun segmentation for a specific video.
    """
    try:
        video_file = VideoFile.objects.get(id=video_id)
        video_file.pipe_1()
        video_file.test_after_pipe_1()
        return Response({'status': 'success', 'message': 'Segmentation rerun successfully'})
    except VideoFile.DoesNotExist:
        return Response({'status': 'error', 'message': 'Video file not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in rerun_segmentation: {e}")
        return Response({'status': 'error', 'message': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
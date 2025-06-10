from pathlib import Path
import os
import mimetypes
from django.http import FileResponse, Http404
from rest_framework import viewsets, decorators, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from ..models import VideoFile, Label, LabelVideoSegment
from ..serializers.video_segmentation import VideoFileSerializer, VideoListSerializer, LabelSerializer, LabelSegmentUpdateSerializer


class VideoViewSet(viewsets.ReadOnlyModelViewSet):
    """
    /api/videos/          → list of metadata   (JSON)
    /api/videos/<id>/     → single metadata   (JSON)
    /videos/<id>/stream/  → raw file          (FileResponse, range-aware)
    """
    queryset = VideoFile.objects.all()
    serializer_class = VideoListSerializer   # for the list view
    permission_classes = [permissions.AllowAny]

    def list(self, request, *args, **kwargs):
        """
        Returns a JSON response with all video metadata and available labels.
        
        The response includes serialized lists of all videos and labels in the database.
        """
        videos = VideoFile.objects.all()
        labels = Label.objects.all()

        video_serializer = VideoListSerializer(videos, many=True)
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
        
        Returns:
            A FileResponse streaming the video file bytes with appropriate headers for
            content type, content length, byte-range requests, and CORS. Raises Http404
            if the video file is missing or not found on disk.
        """
        vf: VideoFile = self.get_object()
        
        # Use active_file_path which handles both processed and raw files
        if hasattr(vf, 'active_file_path') and vf.active_file_path:
            path = Path(vf.active_file_path)
        elif vf.active_file and hasattr(vf.active_file, 'path'):
            try:
                path = Path(vf.active_file.path)
            except ValueError as exc:
                raise Http404("No file associated with this video") from exc
        else:
            raise Http404("No video file available for this entry")

        if not path.exists():
            raise Http404("Video file not found on disk")

        mime, _ = mimetypes.guess_type(str(path))
        response = FileResponse(open(path, 'rb'),
                                content_type=mime or 'video/mp4')
        response['Content-Length'] = path.stat().st_size
        response['Accept-Ranges'] = 'bytes'          # lets the browser seek
        response['Content-Disposition'] = f'inline; filename="{path.name}"'
        
        # CORS headers for frontend compatibility
        frontend_origin = os.environ.get('FRONTEND_ORIGIN', 'http://localhost:8000')
        response["Access-Control-Allow-Origin"] = frontend_origin
        response["Access-Control-Allow-Credentials"] = "true"
        
        return response
    
# Neue separate View für Video-Streaming außerhalb des ViewSets
class VideoStreamView(APIView):
    """
    Separate view for video streaming to avoid DRF content negotiation issues.
    Supports streaming videos from different database entries based on patient examination data.
    """
    permission_classes = [permissions.AllowAny]
    
    def get(self, request, video_id=None):
        """
        Streams the raw video file for the specified video with HTTP range and CORS support.
        
        Args:
            video_id: The ID of the video to stream (can come from patient examination data)
        """
        if video_id is None:
            raise Http404("Video ID is required")
            
        try:
            vf = VideoFile.objects.get(pk=video_id)
        except VideoFile.DoesNotExist:
            raise Http404("Video not found")
        
        # Use active_file_path which handles both processed and raw files
        if hasattr(vf, 'active_file_path') and vf.active_file_path:
            path = Path(vf.active_file_path)
        elif vf.active_file and hasattr(vf.active_file, 'path'):
            try:
                path = Path(vf.active_file.path)
            except ValueError as exc:
                raise Http404("No file associated with this video") from exc
        else:
            raise Http404("No video file available for this entry")

        if not path.exists():
            raise Http404("Video file not found on disk")

        mime, _ = mimetypes.guess_type(str(path))
        response = FileResponse(open(path, 'rb'),
                                content_type=mime or 'video/mp4')
        response['Content-Length'] = path.stat().st_size
        response['Accept-Ranges'] = 'bytes'
        response['Content-Disposition'] = f'inline; filename="{path.name}"'
        
        # CORS headers for frontend compatibility
        frontend_origin = os.environ.get('FRONTEND_ORIGIN', 'http://localhost:8000')
        response["Access-Control-Allow-Origin"] = frontend_origin
        response["Access-Control-Allow-Credentials"] = "true"
        
        return response


# Kept the old VideoView class for backward compatibility during transition
class VideoView(APIView):
    """
    DEPRECATED: Use VideoViewSet instead.
    Legacy API endpoint for backward compatibility.
    """

    def get(self, request, video_id=None):
        """
        Handles GET requests for the legacy video API.
        
        If `video_id` is provided, returns metadata for the specified video; otherwise, returns a list of all videos and labels.
        """
        if video_id is None:
            return self.get_all_videos()
        return self.get_video_details(request, video_id)

    def get_all_videos(self):
        """
        Retrieves all videos and labels and returns them as serialized JSON data.
        
        Returns:
            Response containing serialized lists of videos and labels with HTTP 200 status.
        """
        videos = VideoFile.objects.all()
        labels = Label.objects.all()

        video_serializer = VideoListSerializer(videos, many=True)
        label_serializer = LabelSerializer(labels, many=True)

        return Response({
            "videos": video_serializer.data, 
            "labels": label_serializer.data  
        }, status=status.HTTP_200_OK)

    def get_video_details(self, request, video_id):
        """
        Retrieves metadata for a specific video as a JSON response.
        
        Returns HTTP 200 with serialized video metadata if found, 404 if the video does not exist, or 500 with an error message on unexpected exceptions.
        """
        try:
            video_entry = VideoFile.objects.get(id=video_id)
            serializer = VideoFileSerializer(video_entry, context={'request': request})
            return Response(serializer.data, status=status.HTTP_200_OK)
        except VideoFile.DoesNotExist:
            return Response({"error": "Video not found in the database."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": f"Internal error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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
                    
                    segment_data["frames"][frame_num] = {
                        "frame_filename": frame_filename,
                        "frame_file_path": str(video_entry.frame_dir / frame_filename) if hasattr(video_entry, 'frame_dir') else "",
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

        # Ensure required fields are provided
        required_fields = ["video_id", "label_id", "segments"]
        missing_fields = [field for field in required_fields if field not in request.data]

        if missing_fields:
            return Response({"error": "Missing required fields", "missing": missing_fields}, status=status.HTTP_400_BAD_REQUEST)

        # Validate input data
        serializer = LabelSegmentUpdateSerializer(data=request.data, partial=True)

        if not serializer.is_valid():
            return Response({"error": "Invalid segment data", "details": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        # Process and save segment updates
        result = serializer.save()

        return Response({
            "message": "Segments updated successfully.",
            "updated_segments": result["updated_segments"],
            "new_segments": result["new_segments"],
            "deleted_segments": result["deleted_segments"]
        }, status=status.HTTP_200_OK)
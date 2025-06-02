from pathlib import Path
import os
import mimetypes
from django.http import FileResponse, Http404
from rest_framework import viewsets, decorators, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from ..models import VideoFile, Label
from ..serializers._old.video_segmentation import VideoFileSerializer, VideoListSerializer, LabelSerializer, LabelSegmentUpdateSerializer


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
        Retrieves all available videos and labels.
        
        Returns:
            Response: A JSON response containing serialized lists of videos and labels with HTTP 200 status.
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
        Returns detailed metadata for a specific video as JSON.
        """
        obj = self.get_object()
        return Response(VideoFileSerializer(obj, context={'request': request}).data)

    # ---------- BYTES ---------- #
    @decorators.action(methods=['get'], detail=True,
                       url_path='stream', renderer_classes=[])  # <- disable HTML & JSON renderers
    def stream(self, request, pk=None):
        """
        Streams the video file directly as bytes with range support.
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


# Keep the old VideoView class for backward compatibility during transition
class VideoView(APIView):
    """
    DEPRECATED: Use VideoViewSet instead.
    Legacy API endpoint for backward compatibility.
    """

    def get(self, request, video_id=None):
        """
        Handles GET requests for backward compatibility.
        """
        if video_id is None:
            return self.get_all_videos()
        return self.get_video_details(request, video_id)

    def get_all_videos(self):
        """
        Retrieves all available videos and labels.
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
        Returns video metadata as JSON.
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
        Retrieves time segments and frame-wise predictions for a specific label on a video.
        
        Returns a JSON response containing the label name, its associated time segments, and frame predictions. Responds with HTTP 404 if the video or label is not found, or HTTP 500 for other errors.
        """
        try:
            video_entry = VideoFile.objects.get(id=video_id)
            
            serializer = VideoFileSerializer(video_entry, context={'request': request})
            label_data = serializer.get_label_time_segments(video_entry)

            # Ensure the requested label exists
            if label_name not in label_data:
                return Response({"error": f"Label '{label_name}' not found"}, status=status.HTTP_404_NOT_FOUND)

            return Response({
                "label": label_name,
                "time_segments": label_data[label_name]["time_ranges"],
                "frame_predictions": label_data[label_name]["frame_predictions"]
            }, status=status.HTTP_200_OK)

        except VideoFile.DoesNotExist:
            return Response({"error": "Video not found"}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            return Response({"error": f"Internal error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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
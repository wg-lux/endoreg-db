from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.http import FileResponse, Http404
from ..models import RawVideoFile, Label
from ..serializers.video_segmentation import VideoFileSerializer,VideoListSerializer,LabelSerializer
import mimetypes
import os


class VideoView(APIView):
    """
    API endpoint to:
    - Fetch video metadata (JSON)
    - Serve the actual video file dynamically
    """

    def get(self, request, video_id=None):
        """
        Handles GET requests:
        - If no `video_id` is provided, return a list of all videos for frontend dropdown.
        - If `Accept: application/json` is in the request headers, return metadata for a specific video.
        - Otherwise, return the video file.
        """
        if video_id is None:
            return self.get_all_videos()
        return self.get_video_details(request, video_id)

    def get_all_videos(self):
        """
        Returns a list of all available videos along with available labels.
        Used to populate the video selection dropdown in Vue.js.
        """
        videos = RawVideoFile.objects.all()
        labels = Label.objects.all()  # Fetch all labels

        video_serializer = VideoListSerializer(videos, many=True)
        label_serializer = LabelSerializer(labels, many=True)  # Serialize labels

        return Response({
            "videos": video_serializer.data,  # List of videos
            "labels": label_serializer.data  # List of labels
        }, status=status.HTTP_200_OK)

    def get_video_details(self, request, video_id):
        """
        Returns metadata for a specific video if `Accept: application/json` is set.
        Otherwise, streams the video file.
        """
        try:
            video_entry = RawVideoFile.objects.get(id=video_id)
            serializer = VideoFileSerializer(video_entry, context={'request': request})

            accept_header = request.headers.get('Accept', '')

            if "application/json" in accept_header:
                return Response(serializer.data, status=status.HTTP_200_OK)

            return self.serve_video_file(video_entry)

        except RawVideoFile.DoesNotExist:
            return Response({"error": "Video not found in the database."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": f"Internal error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def serve_video_file(self, video_entry):
        """
        Serves the video file dynamically.
        """
        try:
            full_video_path = video_entry.file.path  # Get the correct file path

            if not os.path.exists(full_video_path):
                raise Http404("Video file not found.")

            mime_type, _ = mimetypes.guess_type(full_video_path)  # Determine the content type (e.g., video/mp4, video/avi)
            response = FileResponse(open(full_video_path, "rb"), content_type=mime_type or "video/mp4")

            # Enable video streaming and CORS
            response["Access-Control-Allow-Origin"] = "*"  # Allow frontend access
            response["Access-Control-Allow-Credentials"] = "true"
            response["Accept-Ranges"] = "bytes"  # Enable seeking in video player
            response["Content-Disposition"] = f'inline; filename="{os.path.basename(full_video_path)}"'  # Instructs the browser to play the video instead of downloading it.

            return response

        except Exception as e:
            return Response({"error": f"Internal error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



from ..serializers.video_segmentation import VideoFileSerializer

class VideoLabelView(APIView):
    """
    API to fetch time segments (start & end times in seconds) for a specific label.
    """

    def get(self, request, video_id, label_name):
        """
        Handles GET request to return:
        - Time segments for the selected label.
        - Frame-wise predictions within those segments.
        """
        try:
            video_entry = RawVideoFile.objects.get(id=video_id)
            
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

        except RawVideoFile.DoesNotExist:
            return Response({"error": "Video not found"}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            return Response({"error": f"Internal error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


from ..serializers.video_segmentation import LabelSegmentUpdateSerializer, LabelSegmentSerializer

class UpdateLabelSegmentsView(APIView):
    """
    API to update or create label segments for a video.
    """

    def put(self, request, video_id, label_id):
        """
        Handles PUT request to update or create label segments.
        """
        serializer = LabelSegmentUpdateSerializer(data=request.data)

        if serializer.is_valid():
            result = serializer.save()
            return Response({
                "message": "Segments updated successfully",
                "updated_segments": result["updated_segments"],
                "new_segments": result["new_segments"]
            }, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

from endoreg_db.models import VideoFile
from endoreg_db.utils.permissions import EnvironmentAwarePermission


from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


class VideoMetadataView(APIView):
    """
    GET /api/video-metadata/{id}/ - Get video metadata including sensitivity analysis
    """
    permission_classes = [EnvironmentAwarePermission]

    def get(self, request, pk):
        video = get_object_or_404(VideoFile, pk=pk)

        # Get or calculate metadata
        metadata = {
            'sensitiveFrameCount': getattr(video, 'sensitive_frame_count', None),
            'totalFrames': getattr(video, 'total_frames', None),
            'sensitiveRatio': getattr(video, 'sensitive_ratio', None),
            'duration': getattr(video, 'duration', None),
            'resolution': getattr(video, 'resolution', None),
        }

        return Response(metadata, status=status.HTTP_200_OK)
from endoreg_db.models import VideoFile
from endoreg_db.serializers.video.video_file_detail import VideoDetailSerializer
from endoreg_db.utils.permissions import EnvironmentAwarePermission


from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


class VideoCorrectionView(APIView):
    """
    GET /api/video/media/video-correction/{id}/ - Get video details for correction
    """
    permission_classes = [EnvironmentAwarePermission]

    def get(self, request, pk):
        video = get_object_or_404(VideoFile, pk=pk)
        ser = VideoDetailSerializer(video, context={"request": request})
        return Response(ser.data, status=status.HTTP_200_OK)
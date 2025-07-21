from endoreg_db.models import VideoFile
from endoreg_db.utils.permissions import EnvironmentAwarePermission


from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


class VideoProcessingHistoryView(APIView):
    """
    GET /api/video-processing-history/{id}/ - Get processing history for a video
    """
    permission_classes = [EnvironmentAwarePermission]

    def get(self, request, pk):
        _video = get_object_or_404(VideoFile, pk=pk)

        # For now, return empty history - can be extended with actual history tracking
        #TODO plan when to implement this
        history = []

        return Response(history, status=status.HTTP_200_OK)
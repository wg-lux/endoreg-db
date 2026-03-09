from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.models import VideoFile
from endoreg_db.utils.permissions import EnvironmentAwarePermission


class VideoFpsView(APIView):
    """
    GET media/videos/{pk}/fps/ - Return fps for a single video.
    """

    permission_classes = [EnvironmentAwarePermission]

    def get(self, request, pk):
        video = get_object_or_404(VideoFile, pk=pk)
        try:
            fps = float(video.get_fps())
        except Exception as exc:
            return Response(
                {
                    "error": "Could not determine fps for the requested video.",
                    "details": {"video_id": pk, "reason": str(exc)},
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        return Response(
            {
                "video_id": video.pk,
                "fps": fps,
            },
            status=status.HTTP_200_OK,
        )

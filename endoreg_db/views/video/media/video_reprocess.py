from endoreg_db.models import VideoFile
from endoreg_db.utils.permissions import EnvironmentAwarePermission


from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


class VideoReprocessView(APIView):
    """
    POST /api/video-reprocess/{id}/ - Reprocess video with updated settings
    """
    permission_classes = [EnvironmentAwarePermission]

    def post(self, request, pk):
        video = get_object_or_404(VideoFile, pk=pk)

        try:
            # Reset video processing status
            if hasattr(video, 'processing_status'):
                video.processing_status = 'not_started'
                video.save()

            # Start reprocessing task
            from endoreg_db.tasks.video_processing_tasks import reprocess_video_task

            task = reprocess_video_task.delay(video_id=pk)

            return Response(
                {"task_id": task.id, "status": "reprocessing_started"},
                status=status.HTTP_202_ACCEPTED
            )

        except Exception as e:
            return Response(
                {"error": f"Failed to start reprocessing: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
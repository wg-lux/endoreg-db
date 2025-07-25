from endoreg_db.models import VideoFile
from endoreg_db.utils.permissions import EnvironmentAwarePermission


from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


class VideoRemoveFramesView(APIView):
    """
    POST /api/video-remove-frames/{id}/ - Remove frames from video using streaming processing
    """
    permission_classes = [EnvironmentAwarePermission]

    def post(self, request, pk):
        video = get_object_or_404(VideoFile, pk=pk)

        selection_method = request.data.get('selection_method', 'automatic')
        detection_engine = request.data.get('detection_engine', 'minicpm')
        use_streaming = request.data.get('use_streaming', True)
        manual_frames = request.data.get('manual_frames')

        try:
            # Start async task for frame removal
            from endoreg_db.tasks.video_processing_tasks import remove_video_frames_task

            task_data = {
                'video_id': pk,
                'selection_method': selection_method,
                'detection_engine': detection_engine,
                'use_streaming': use_streaming,
                'manual_frames': manual_frames
            }

            task = remove_video_frames_task.delay(**task_data)

            return Response(
                {"task_id": task.id, "status": "started"},
                status=status.HTTP_202_ACCEPTED
            )

        except Exception as e:
            return Response(
                {"error": f"Failed to start frame removal task: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
from endoreg_db.models import VideoFile
from endoreg_db.utils.permissions import EnvironmentAwarePermission


from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


class VideoApplyMaskView(APIView):
    """
    POST /api/video-apply-mask/{id}/ - Apply mask to video using streaming processing
    """
    permission_classes = [EnvironmentAwarePermission]

    def post(self, request, pk):
        video = get_object_or_404(VideoFile, pk=pk)

        mask_type = request.data.get('mask_type', 'device_default')
        device_name = request.data.get('device_name', 'olympus_cv_1500')
        use_streaming = request.data.get('use_streaming', True)
        custom_mask = request.data.get('custom_mask')

        try:
            # Start async task for video masking
            from endoreg_db.tasks.video_processing_tasks import apply_video_mask_task

            task_data = {
                'video_id': pk,
                'mask_type': mask_type,
                'device_name': device_name,
                'use_streaming': use_streaming,
                'custom_mask': custom_mask
            }

            task = apply_video_mask_task.delay(**task_data)

            return Response(
                {"task_id": task.id, "status": "started"},
                status=status.HTTP_202_ACCEPTED
            )

        except Exception as e:
            return Response(
                {"error": f"Failed to start masking task: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
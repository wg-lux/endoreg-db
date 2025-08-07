from endoreg_db.models import VideoFile
from endoreg_db.utils.permissions import EnvironmentAwarePermission

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


class VideoAnalyzeView(APIView):
    """
    POST /api/video-analyze/{id}/ - Analyze video for sensitive content
    """
    permission_classes = [EnvironmentAwarePermission]

    def post(self, request, pk):
        video = get_object_or_404(VideoFile, pk=pk)
        use_minicpm = request.data.get('use_minicpm', True)
        _detailed_analysis = request.data.get('detailed_analysis', True)

        try:
            # Import FrameCleaner here to avoid circular imports
            from lx_anonymizer.frame_cleaner import FrameCleaner

            cleaner = FrameCleaner(
                video_path=video.file.path,
                use_minicpm=use_minicpm
            )

            # Perform analysis
            analysis_result = cleaner.analyze_video_sensitivity()

            # Update video metadata
            if hasattr(video, 'sensitive_frame_count'):
                video.sensitive_frame_count = analysis_result.get('sensitive_frames', 0)
            if hasattr(video, 'total_frames'):
                video.total_frames = analysis_result.get('total_frames', 0)
            if hasattr(video, 'sensitive_ratio'):
                video.sensitive_ratio = analysis_result.get('sensitivity_ratio', 0.0)

            try:
                video.save()
            except Exception as e:
                print(f"Warning: Could not save video metadata: {e}")

            return Response(analysis_result, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"error": f"Analysis failed: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
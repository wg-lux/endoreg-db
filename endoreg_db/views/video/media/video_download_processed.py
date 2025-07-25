from endoreg_db.utils.permissions import EnvironmentAwarePermission


from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


import os


class VideoDownloadProcessedView(APIView):
    """
    GET /api/video-download-processed/{id}/ - Download processed video result
    """
    permission_classes = [EnvironmentAwarePermission]

    def get(self, request, pk):
        # Remove unused 'video' variable
        output_path = request.query_params.get('path')

        # Define the allowed base directory for processed videos
        processed_base_dir = os.path.abspath(os.getenv("PROCESSED_VIDEO_DIR", "/srv/processed_videos"))
        if not output_path:
            return Response(
                {"error": "Processed file not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Resolve the absolute path and check if it's within the allowed directory
        abs_output_path = os.path.abspath(output_path)
        if not abs_output_path.startswith(processed_base_dir + os.sep):
            return Response(
                {"error": "Invalid file path"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not os.path.exists(abs_output_path):
            return Response(
                {"error": "Processed file not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        try:
            from django.http import FileResponse
            with open(abs_output_path, 'rb') as f:
                response = FileResponse(
                    f,
                    content_type='video/mp4'
                )
                response['Content-Disposition'] = f'attachment; filename="{os.path.basename(abs_output_path)}"'
                return response

        except Exception as e:
            return Response(
                {"error": f"Failed to serve file: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
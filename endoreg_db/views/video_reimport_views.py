from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.http import Http404
from django.db import transaction
import logging

from ..models import VideoFile
from ..services.video_import import import_and_anonymize

logger = logging.getLogger(__name__)


class VideoReimportView(APIView):
    """
    API endpoint to re-import a video file and regenerate metadata.
    This is useful when OCR failed or metadata is incomplete.
    """

    def post(self, request, video_id):
        """
        Re-import a video file to regenerate SensitiveMeta and other metadata.
        """
        try:
            video = VideoFile.objects.get(id=video_id)
        except VideoFile.DoesNotExist:
            return Response(
                {"error": f"Video with ID {video_id} not found."}, 
                status=status.HTTP_404_NOT_FOUND
            )

        if not video.raw_file:
            return Response(
                {"error": "Video has no raw file to re-import."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Log the re-import attempt
            logger.info(f"Starting re-import for video {video.uuid} (ID: {video_id})")
            
            with transaction.atomic():
                # Clear existing metadata to force regeneration
                if video.sensitive_meta:
                    logger.info(f"Clearing existing SensitiveMeta for video {video.uuid}")
                    video.sensitive_meta = None
                    video.save(update_fields=['sensitive_meta'])
                
                # Re-run the import process
                center_name = video.center.name if video.center else "university_hospital_wuerzburg"
                processor_name = video.processor.name if video.processor else "olympus_cv_1500"
                
                logger.info(f"Re-importing video {video.uuid} with center={center_name}, processor={processor_name}")
                
                # Use the existing import service
                reimported_video = import_and_anonymize(
                    file_path=video.raw_file.path,
                    center_name=center_name,
                    processor_name=processor_name,
                    save_video=False,  # Don't move the file again
                    delete_source=False  # Don't delete the existing file
                )
                
                logger.info(f"Re-import completed successfully for video {video.uuid}")
                
                return Response({
                    "message": "Video re-import started successfully.",
                    "video_id": video_id,
                    "uuid": str(reimported_video.uuid)
                }, status=status.HTTP_200_OK)
                
        except Exception as e:
            logger.error(f"Error during video re-import for ID {video_id}: {str(e)}", exc_info=True)
            return Response(
                {"error": f"Re-import failed: {str(e)}"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
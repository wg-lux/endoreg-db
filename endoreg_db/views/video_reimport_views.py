from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.http import Http404
from django.db import transaction
import logging
from pathlib import Path

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
        # Validate video_id parameter
        if not video_id or not isinstance(video_id, int):
            return Response(
                {"error": "Invalid video ID provided."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            video = VideoFile.objects.get(id=video_id)
            logger.info(f"Found video {video.uuid} (ID: {video_id}) for re-import")
        except VideoFile.DoesNotExist:
            logger.warning(f"Video with ID {video_id} not found")
            return Response(
                {"error": f"Video with ID {video_id} not found."}, 
                status=status.HTTP_404_NOT_FOUND
            )

        # Check if video has a raw file
        if not video.raw_file:
            logger.warning(f"Video {video.uuid} has no raw file")
            return Response(
                {"error": "Video has no raw file to re-import."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if the raw file actually exists on disk
        raw_file_path = Path(video.raw_file.path)
        if not raw_file_path.exists():
            logger.error(f"Raw file not found on disk: {raw_file_path}")
            return Response(
                {"error": f"Video file not found on server: {raw_file_path.name}"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if video has required relationships
        if not video.center:
            logger.warning(f"Video {video.uuid} has no associated center")
            return Response(
                {"error": "Video has no associated center."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Log the re-import attempt
            logger.info(f"Starting re-import for video {video.uuid} (ID: {video_id})")
            
            # Don't use transaction.atomic() for the entire operation
            # as import_and_anonymize might take a long time
            
            # Clear existing metadata to force regeneration
            if video.sensitive_meta:
                logger.info(f"Clearing existing SensitiveMeta for video {video.uuid}")
                old_meta_id = video.sensitive_meta.id
                video.sensitive_meta = None
                video.save(update_fields=['sensitive_meta'])
                logger.info(f"Cleared SensitiveMeta {old_meta_id} from video {video.uuid}")
            
            # Get center and processor info
            center_name = video.center.name if video.center else "university_hospital_wuerzburg"
            processor_name = video.processor.name if video.processor else "olympus_cv_1500"
            
            logger.info(f"Re-importing video {video.uuid} with center={center_name}, processor={processor_name}")
            
            # Use the existing import service
            reimported_video = import_and_anonymize(
                file_path=str(raw_file_path),
                center_name=center_name,
                processor_name=processor_name,
                save_video=False,  # Don't move the file again
                delete_source=False  # Don't delete the existing file
            )
            
            logger.info(f"Re-import completed successfully for video {video.uuid}")
            
            return Response({
                "message": "Video re-import completed successfully.",
                "video_id": video_id,
                "original_uuid": str(video.uuid),
                "reimported_uuid": str(reimported_video.uuid),
                "sensitive_meta_created": reimported_video.sensitive_meta is not None
            }, status=status.HTTP_200_OK)
                
        except FileNotFoundError as e:
            logger.error(f"File not found during re-import for ID {video_id}: {str(e)}")
            return Response(
                {"error": f"Required file not found: {str(e)}"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        except ValueError as e:
            logger.error(f"Invalid data during re-import for ID {video_id}: {str(e)}")
            return Response(
                {"error": f"Invalid data: {str(e)}"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.error(f"Unexpected error during video re-import for ID {video_id}: {str(e)}", exc_info=True)
            return Response(
                {"error": f"Re-import failed: {str(e)}"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.http import Http404
from django.db import transaction
import logging
from pathlib import Path

from ..models import VideoFile, SensitiveMeta
from ..services.video_import import _ensure_default_patient_data

logger = logging.getLogger(__name__)


class VideoReimportView(APIView):
    """
    API endpoint to re-import a video file and regenerate metadata.
    This is useful when OCR failed or metadata is incomplete.
    """

    def post(self, request, video_id):
        """
        Re-import a video file to regenerate SensitiveMeta and other metadata.
        Instead of creating a new video, this updates the existing one.
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
            logger.info(f"Starting in-place re-import for video {video.uuid} (ID: {video_id})")
            
            with transaction.atomic():
                # Clear existing metadata to force regeneration
                old_meta_id = None
                if video.sensitive_meta:
                    old_meta_id = video.sensitive_meta.id
                    logger.info(f"Clearing existing SensitiveMeta {old_meta_id} for video {video.uuid}")
                    video.sensitive_meta = None
                    video.save(update_fields=['sensitive_meta'])
                    
                    # Delete the old SensitiveMeta record
                    try:
                        SensitiveMeta.objects.filter(id=old_meta_id).delete()
                        logger.info(f"Deleted old SensitiveMeta {old_meta_id}")
                    except Exception as e:
                        logger.warning(f"Could not delete old SensitiveMeta {old_meta_id}: {e}")
                
                # Re-initialize video specs and frames
                logger.info(f"Re-initializing video specs for {video.uuid}")
                video.initialize_video_specs()
                video.initialize_frames()
                
                # Run Pipe 1 for OCR and AI processing
                logger.info(f"Starting Pipe 1 processing for {video.uuid}")
                success = video.pipe_1(
                    model_name="image_multilabel_classification_colonoscopy_default",
                    delete_frames_after=True,
                    ocr_frame_fraction=0.01,
                    ocr_cap=5
                )
                
                if not success:
                    logger.error(f"Pipe 1 processing failed for video {video.uuid}")
                    return Response(
                        {"error": "OCR and AI processing failed during re-import."}, 
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
                
                logger.info(f"Pipe 1 processing completed for {video.uuid}")
                
                # Ensure minimum patient data is available
                logger.info(f"Ensuring minimum patient data for {video.uuid}")
                _ensure_default_patient_data(video)
                
                # Refresh from database to get updated data
                video.refresh_from_db()
                
            logger.info(f"In-place re-import completed successfully for video {video.uuid}")
            
            return Response({
                "message": "Video re-import completed successfully.",
                "video_id": video_id,
                "uuid": str(video.uuid),
                "sensitive_meta_created": video.sensitive_meta is not None,
                "sensitive_meta_id": video.sensitive_meta.id if video.sensitive_meta else None,
                "updated_in_place": True
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
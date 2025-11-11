from celery.events import state
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
import logging
from pathlib import Path
from django.db import transaction
from ...models import VideoFile, SensitiveMeta
from ...services.video_import import VideoImportService
logger = logging.getLogger(__name__)

class VideoReimportView(APIView):
    """
    API endpoint to re-import a video file and regenerate metadata.
    This is useful when OCR failed or metadata is incomplete.
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.video_service = VideoImportService()

    def post(self, request, pk):
        """
        Re-import a video file to regenerate SensitiveMeta and other metadata.
        Instead of creating a new video, this updates the existing one.
        
        Args:
            pk (int): Primary key of the VideoFile to reimport
        """
        # Validate pk parameter
        if not pk or not isinstance(pk, int):
            return Response(
                {"error": "Invalid video ID provided."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            video = VideoFile.objects.get(id=pk)
            logger.info(f"Found video {video.uuid} (ID: {pk}) for re-import")
        except VideoFile.DoesNotExist:
            logger.warning(f"Video with ID {pk} not found")
            return Response(
                {"error": f"Video with ID {pk} not found."}, 
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
            logger.info(f"Starting in-place re-import for video {video.uuid} (ID: {pk})")
            
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
            
                try:
                    success = video.pipe_1(
                        model_name="image_multilabel_classification_colonoscopy_default",
                        delete_frames_after=True,
                        ocr_frame_fraction=0.01,
                        ocr_cap=5
                    )
                except Exception as e:
                    logger.error(f"Pipe 1 processing raised exception for {video.uuid}: {e}")
                    return Response(
                        {"error": f"OCR and AI processing failed: {str(e)}"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
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
                self.video_service._ensure_default_patient_data(video)
                
                # Refresh from database to get updated data
                video.refresh_from_db()
                
                # Use VideoImportService for anonymization
                try:
                    processor_name = video.video_meta.processor.name if video.video_meta and video.video_meta.processor else "Unknown"
                    logger.info(f"Starting anonymization using VideoImportService for {video.uuid}")
                    self.video_service.import_and_anonymize(
                        file_path=raw_file_path,
                        center_name=video.center.name,
                        processor_name=processor_name,
                        save_video=True,
                        delete_source=False
                    )
                    
                    logger.info(f"VideoImportService anonymization completed for {video.uuid}")
                    
                    
                    return Response({
                        "message": "Video re-import with VideoImportService completed successfully.",
                        "video_id": pk,
                        "uuid": str(video.uuid),
                        "frame_cleaning_applied": True,
                        "sensitive_meta_created": video.sensitive_meta is not None,
                        "sensitive_meta_id": video.sensitive_meta.id if video.sensitive_meta else None,
                        "updated_in_place": True,
                        "status": "done"
                    }, status=status.HTTP_200_OK)
                    
                except Exception as e:
                    logger.exception(f"VideoImportService anonymization failed for video {video.uuid}: {e}")
                    logger.warning("Continuing without anonymization due to error")
                
                state.mark_sensitive_meta_processed(save=True)
                
            # If we reach here, everything was successful
            logger.info(f"Video re-import completed successfully for {video.uuid}")
            video.save(update_fields=['sensitive_meta', 'date_modified'])
            
            return Response({
                "message": "Video re-import completed successfully.",
                "video_id": pk,
                "uuid": str(video.uuid),
                "sensitive_meta_created": video.sensitive_meta is not None,
                "sensitive_meta_id": video.sensitive_meta.id if video.sensitive_meta else None,
                "updated_in_place": True,
                "status": "done"  # ⭐ Add explicit done status
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Failed to re-import video {video.uuid}: {str(e)}", exc_info=True)
            
            # Handle specific error types
            error_msg = str(e)
            if any(phrase in error_msg.lower() for phrase in ["insufficient storage", "no space left", "disk full"]):
                # Storage error - return specific error message
                return Response({
                    "error": f"Storage error during re-import: {error_msg}",
                    "error_type": "storage_error",
                    "video_id": pk,
                    "uuid": str(video.uuid)
                }, status=status.HTTP_507_INSUFFICIENT_STORAGE)
            else:
                # Other errors
                return Response({
                    "error": f"Re-import failed: {error_msg}",
                    "error_type": "processing_error", 
                    "video_id": pk,
                    "uuid": str(video.uuid)
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

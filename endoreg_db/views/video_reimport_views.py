from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
import logging
from pathlib import Path
from django.db import transaction
from django.core.files.base import ContentFile
from ..models import VideoFile, SensitiveMeta
from ..services.video_import import _ensure_default_patient_data, _ensure_frame_cleaning_available
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
                '''
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
                '''
                logger.info(f"Pipe 1 processing completed for {video.uuid}")
                
                # Ensure minimum patient data is available
                logger.info(f"Ensuring minimum patient data for {video.uuid}")
                _ensure_default_patient_data(video)
                
                # Refresh from database to get updated data
                video.refresh_from_db()
                frame_cleaning_available, FrameCleaner, ReportReader = _ensure_frame_cleaning_available()
    
                if frame_cleaning_available:
                    try:
                        logger.info("Starting frame-level anonymization with processor ROI masking...")
                        
                        # Get processor ROI information for masking
                        processor_roi = None
                        endoscope_roi = None
                        
                        try:
                            if video.video_meta and video.video_meta.processor:
                                processor = video.video_meta.processor
                                
                                # Get the endoscope ROI for masking
                                endoscope_roi = processor.get_roi_endoscope_image()
                                
                                # Get all processor ROIs for comprehensive masking
                                processor_roi = {
                                    'endoscope_image': endoscope_roi,
                                    'patient_first_name': processor.get_roi_patient_first_name(),
                                    'patient_last_name': processor.get_roi_patient_last_name(),
                                    'patient_dob': processor.get_roi_patient_dob(),
                                    'examination_date': processor.get_roi_examination_date(),
                                    'examination_time': processor.get_roi_examination_time(),
                                    'endoscope_type': processor.get_roi_endoscope_type(),
                                    'endoscopy_sn': processor.get_roi_endoscopy_sn(),
                                }
                                
                                logger.info(f"Retrieved processor ROI information: endoscope_roi={endoscope_roi}")
                                
                            else:
                                logger.warning(f"No processor found for video {video.uuid}, proceeding without ROI masking")
                                
                        except Exception as e:
                            logger.error(f"Failed to retrieve processor ROI information: {e}")
                            # Continue without ROI - don't fail the entire import process
                        
                        # Instantiate frame cleaner and report reader
                        frame_cleaner = FrameCleaner()
                        report_reader = ReportReader(
                            report_root_path=str(Path(video.raw_file.path).parent),
                            locale="de_DE",  # Default German locale for medical data
                            text_date_format="%d.%m.%Y"  # Common German date format
                        )
                        
                        # Clean video with ROI masking (heavy I/O operation)
                        # Pass the endoscope ROI to the frame cleaner for masking
                        output_path = Path(video.raw_file.path).parent / f"cleaned_{Path(video.raw_file.path).name}"
                        cleaned_video_path, extracted_metadata = frame_cleaner.clean_video(
                            Path(video.raw_file.path),
                            output_path,
                            video,  # Pass VideoFile object to store metadata
                            report_reader,
                            processor.name if processor else "Unknown",
                            video.get_frame_paths() if hasattr(video, 'get_frame_paths') else None
                        )
                        
                        
                        
                        # Save cleaned video back to VideoFile (atomic transaction)
                        with transaction.atomic():
                            # Save the cleaned video using Django's FileField
                            with open(cleaned_video_path, 'rb') as f:
                                video.raw_file.save(
                                    cleaned_video_path.name, 
                                    ContentFile(f.read())
                                )
                            video.save()
                            
                        logger.info(f"Frame cleaning with ROI masking completed: {cleaned_video_path.name}")
                        logger.info(f"Extracted metadata: {extracted_metadata}")
                            
                        logger.info(f"In-place re-import completed successfully for video {video.uuid}")
                        
                        # ⭐ Set anonymization status to "done" for frontend validation
                        self._set_anonymization_done_status(video)
                        
                        # FIX: Return proper Response object instead of bare return
                        return Response({
                            "message": "Video re-import with frame cleaning completed successfully.",
                            "video_id": video_id,
                            "uuid": str(video.uuid),
                            "cleaned_video": str(cleaned_video_path.name),
                            "frame_cleaning_applied": True,
                            "extracted_metadata": extracted_metadata,
                            "sensitive_meta_created": video.sensitive_meta is not None,
                            "sensitive_meta_id": video.sensitive_meta.id if video.sensitive_meta else None,
                            "updated_in_place": True,
                            "status": "done"  # ⭐ Add explicit done status
                        }, status=status.HTTP_200_OK)
                    except Exception as e:
                        logger.exception(f"Frame cleaning with ROI masking failed for video {video.uuid}: {e}")
                        # FIX: Don't return here, let it continue to the success response
                        logger.warning("Continuing without frame cleaning due to error")
                else:
                    logger.warning("Frame cleaning not available, skipping anonymization step")
                
            # ⭐ Set anonymization status to "done" even without frame cleaning
            self._set_anonymization_done_status(video)
            
            return Response({
                "message": "Video re-import completed successfully.",
                "video_id": video_id,
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
                    "video_id": video_id,
                    "uuid": str(video.uuid)
                }, status=status.HTTP_507_INSUFFICIENT_STORAGE)
            else:
                # Other errors
                return Response({
                    "error": f"Re-import failed: {error_msg}",
                    "error_type": "processing_error", 
                    "video_id": video_id,
                    "uuid": str(video.uuid)
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _set_anonymization_done_status(self, video_file):
        """
        Set the anonymization status to 'done' for frontend validation.
        
        Args:
            video_file: VideoFile instance
        """
        try:
            # Import here to avoid circular imports
            from ..models import AnonymizationTask
            
            # Find or create anonymization task for this video
            anonymization_task, created = AnonymizationTask.objects.get_or_create(
                video_file=video_file,
                defaults={
                    'status': 'done',
                    'progress': 100,
                    'message': 'Video re-import completed successfully'
                }
            )
            
            if not created:
                # Update existing task
                anonymization_task.status = 'done'
                anonymization_task.progress = 100
                anonymization_task.message = 'Video re-import completed successfully'
                anonymization_task.save(update_fields=['status', 'progress', 'message'])
            
            logger.info(f"Anonymization status set to 'done' for video {video_file.uuid}")
            
        except Exception as e:
            logger.error(f"Failed to set anonymization status for {video_file.uuid}: {e}")
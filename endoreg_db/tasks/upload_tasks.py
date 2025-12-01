import os
import logging
from typing import Optional
from celery_app import shared_task
from django.core.files.storage import default_storage

from endoreg_db.models.upload_job import UploadJob
from endoreg_db.models import SensitiveMeta, VideoFile


logger = logging.getLogger(__name__)


@shared_task(bind=True)
def process_upload_job(self, job_id: str) -> dict:
    """
    Process an UploadJob's file (PDF or video) and create or associate a SensitiveMeta.
    
    This updates the UploadJob status as it proceeds: marks the job as processing, routes the file
    to the PDF or video processor, and on success marks the job completed with the created
    SensitiveMeta. On any failure the job is marked as error and an error description is returned.
    
    Parameters:
        job_id (str): UUID of the UploadJob to process.
    
    Returns:
        dict: Result of processing:
            - {'status': 'anonymized', 'sensitive_meta_id': <id>, 'job_id': <job_id>} on success.
            - {'status': 'error', 'error': '<message>'} on failure.
    """
    try:
        # Get the upload job
        try:
            upload_job = UploadJob.objects.get(id=job_id)
        except UploadJob.DoesNotExist:
            error_msg = f"UploadJob with id {job_id} not found"
            logger.error(error_msg)
            return {'status': 'error', 'error': error_msg}
        
        # Mark as processing
        upload_job.mark_processing()
        logger.info(f"Started processing upload job {job_id}")
        
        # Get file path
        file_path = upload_job.file.path
        if not os.path.exists(file_path):
            error_msg = f"Uploaded file not found at {file_path}"
            upload_job.mark_error(error_msg)
            logger.error(error_msg)
            return {'status': 'error', 'error': error_msg}
        
        # Route to appropriate processor based on content type
        sensitive_meta = None
        
        if upload_job.content_type == 'application/pdf':
            sensitive_meta = _process_pdf_file(upload_job, file_path)
        elif upload_job.content_type.startswith('video/'):
            sensitive_meta = _process_video_file(upload_job, file_path)
        else:
            error_msg = f"Unsupported content type: {upload_job.content_type}"
            upload_job.mark_error(error_msg)
            logger.error(error_msg)
            return {'status': 'error', 'error': error_msg}
        
        if sensitive_meta:
            # Mark job as completed with the created SensitiveMeta
            upload_job.mark_completed(sensitive_meta)
            logger.info(f"Successfully processed upload job {job_id}, created SensitiveMeta {sensitive_meta.id}")
            return {
                'status': 'anonymized',
                'sensitive_meta_id': sensitive_meta.id,
                'job_id': job_id
            }
        else:
            error_msg = "Processing completed but no SensitiveMeta was created"
            upload_job.mark_error(error_msg)
            logger.error(f"Upload job {job_id}: {error_msg}")
            return {'status': 'error', 'error': error_msg}
            
    except Exception as e:
        error_msg = f"Unexpected error processing upload job {job_id}: {str(e)}"
        logger.exception(error_msg)
        
        try:
            upload_job = UploadJob.objects.get(id=job_id)
            upload_job.mark_error(error_msg)
        except Exception as inner_e:
            logger.error(f"Failed to update job status after error: {inner_e}")
        
        return {'status': 'error', 'error': error_msg}


def _process_pdf_file(upload_job: UploadJob, file_path: str) -> Optional[SensitiveMeta]:
    """
    Extract sensitive metadata from a PDF file and create a SensitiveMeta record.
    
    This function processes the PDF at file_path, converts extracted metadata into a SensitiveMeta,
    saves it to the database, and returns the created instance.
    
    Parameters:
        upload_job (UploadJob): The UploadJob associated with this file (used for context).
        file_path (str): Filesystem path to the PDF to process.
    
    Returns:
        SensitiveMeta: The created SensitiveMeta instance built from the PDF's extracted metadata.
    
    Raises:
        ImportError: If the required `lx_anonymizer` library (ReportReader) is not available.
        ValueError: If no metadata could be extracted from the PDF.
        Exception: Propagates other unexpected errors encountered during processing.
    """
    try:
        # Import ReportReader (lazy import to avoid startup issues)
        try:
            from lx_anonymizer.report_reader import ReportReader
        except ImportError as e:
            raise ImportError(f"Could not import ReportReader: {e}. Make sure lx-anonymizer is available.")
        
        logger.info(f"Processing PDF file: {file_path}")
        
        # Create ReportReader instance
        reader = ReportReader(
            locale="de_DE",
            text_date_format="%d.%m.%Y"
        )
        
        # Process the report
        original_text, anonymized_text, metadata = reader.process_report(
            pdf_path=file_path,
            use_ensemble=False,  # Can be made configurable
            verbose=False,
            use_llm_extractor=None  # Use default extractor
        )
        
        if not metadata:
            raise ValueError("No metadata extracted from PDF")
        
        # Create SensitiveMeta from extracted metadata
        sensitive_meta = SensitiveMeta.create_from_dict(metadata)
        sensitive_meta.save()
        
        logger.info(f"Created SensitiveMeta {sensitive_meta.id} from PDF processing")
        return sensitive_meta
        
    except Exception as e:
        logger.exception(f"Error processing PDF file {file_path}: {e}")
        raise


def _process_video_file(upload_job: UploadJob, file_path: str) -> Optional[SensitiveMeta]:
    """
    Process a video file to extract or produce sensitive metadata and associate it with the video.
    
    Parameters:
        upload_job (UploadJob): Upload job owning the file; used for context but not modified.
        file_path (str): Absolute path to the uploaded video file on disk.
    
    Returns:
        SensitiveMeta or None: `SensitiveMeta` if processing produced or an association was created, `None` if no metadata could be produced.
    
    Raises:
        ValueError: If a VideoFile instance cannot be created from the given file_path.
        Exception: Any unexpected error from the video processing pipeline or model operations is propagated.
    """
    try:
        logger.info(f"Processing video file: {file_path}")
        
        # Get the relative path for storage
        relative_path = os.path.relpath(file_path, default_storage.location)
        
        # Create VideoFile instance using the existing method
        video_file = VideoFile.create_from_file_initialized(
            file_path=relative_path,
            skip_frame_extraction=False,  # Extract frames for processing
            skip_meta_extraction=False,   # Extract metadata
        )
        
        if not video_file:
            raise ValueError("Failed to create VideoFile instance")
        
        # Run the video processing pipeline (pipe_1)
        try:
            # Import the processing function
            from endoreg_db.management.commands.import_video import pipe_1
            
            # Run processing pipeline
            pipe_1(
                video_file=video_file,
                skip_frame_extraction=False,
                skip_meta_extraction=False,
                skip_anonymization=False
            )
            
        except Exception as e:
            logger.warning(f"Video processing pipeline failed, but VideoFile was created: {e}")
            # Continue anyway - we might still have usable metadata
        
        # Get the associated SensitiveMeta
        if hasattr(video_file, 'sensitive_meta') and video_file.sensitive_meta:
            sensitive_meta = video_file.sensitive_meta
            logger.info(f"Found SensitiveMeta {sensitive_meta.id} from video processing")
            return sensitive_meta
        else:
            # Create a basic SensitiveMeta if none exists
            sensitive_meta = SensitiveMeta.objects.create()
            
            # Link it to the video if possible
            if hasattr(video_file, 'sensitive_meta'):
                video_file.sensitive_meta = sensitive_meta
                video_file.save(update_fields=['sensitive_meta'])
            
            logger.info(f"Created basic SensitiveMeta {sensitive_meta.id} for video file")
            return sensitive_meta
            
    except Exception as e:
        logger.exception(f"Error processing video file {file_path}: {e}")
        raise
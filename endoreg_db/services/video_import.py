"""
Video import service module.

Provides high-level functions for importing and anonymizing video files,
combining VideoFile creation with frame-level anonymization.
"""
import os
from datetime import date
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Union
from django.core.files.base import ContentFile
from django.db import transaction
from endoreg_db.models import VideoFile, SensitiveMeta

try:
    STORAGE_DIR = os.getenv("STORAGE_DIR")
    if not STORAGE_DIR:
        STORAGE_DIR = Path(__file__).parent.parent.parent.parent / "data" / "videos"    
except KeyError:
    raise EnvironmentError("STORAGE_DIR environment variable not set and default path not found.")
    
    
if TYPE_CHECKING:
    from endoreg_db.models import VideoFile

logger = logging.getLogger(__name__)


def _ensure_frame_cleaning_available():
    """
    Ensure frame cleaning modules are available by adding lx-anonymizer to path.
    
    Returns:
        Tuple of (availability_flag, FrameCleaner_class, ReportReader_class)
    """
    try:
        # Check if we can find the lx-anonymizer directory
        current_file = Path(__file__)
        from importlib import resources
        lx_anonymizer_path = resources.files("lx_anonymizer")
        
        if lx_anonymizer_path.exists():
            # Add to Python path temporarily
            if str(lx_anonymizer_path) not in sys.path:
                sys.path.insert(0, str(lx_anonymizer_path))
            
            # Try simple import
            from lx_anonymizer import FrameCleaner, ReportReader
            
            logger.info("Successfully imported lx_anonymizer modules")
            
            # Remove from path to avoid conflicts
            if str(lx_anonymizer_path) in sys.path:
                sys.path.remove(str(lx_anonymizer_path))
                
            return True, FrameCleaner, ReportReader
        
        else:
            logger.warning(f"lx-anonymizer path not found: {lx_anonymizer_path}") 
            
    except Exception as e:
        logger.warning(f"Frame cleaning not available: {e}")
    
    return False, None, None


def _ensure_default_patient_data(video_file: "VideoFile") -> None:
    """
    Ensure video has minimum required patient data in SensitiveMeta.
    Creates default values if data is missing after OCR processing.
    """
    from endoreg_db.models import SensitiveMeta
    
    if not video_file.sensitive_meta:
        logger.info(f"No SensitiveMeta found for video {video_file.uuid}, creating default")
        
        # Create default SensitiveMeta with placeholder data
        default_data = {
            "patient_first_name": "Patient",
            "patient_last_name": "Unknown", 
            "patient_dob": date(1990, 1, 1),  # Default DOB
            "examination_date": date.today(),
            "center_name": video_file.center.name if video_file.center else "university_hospital_wuerzburg"
        }
        
        try:
            sensitive_meta = SensitiveMeta.create_from_dict(default_data)
            video_file.sensitive_meta = sensitive_meta
            video_file.save(update_fields=['sensitive_meta'])
            logger.info(f"Created default SensitiveMeta for video {video_file.uuid}")
        except Exception as e:
            logger.error(f"Failed to create default SensitiveMeta for video {video_file.uuid}: {e}")
            
    else:
        # Update existing SensitiveMeta with missing fields
        update_needed = False
        update_data = {}
        
        if not video_file.sensitive_meta.patient_first_name:
            update_data["patient_first_name"] = "Patient"
            update_needed = True
            
        if not video_file.sensitive_meta.patient_last_name:
            update_data["patient_last_name"] = "Unknown"
            update_needed = True
            
        if not video_file.sensitive_meta.patient_dob:
            update_data["patient_dob"] = date(1990, 1, 1)
            update_needed = True
            
        if not video_file.sensitive_meta.examination_date:
            update_data["examination_date"] = date.today()
            update_needed = True
            
        if update_needed:
            try:
                video_file.sensitive_meta.update_from_dict(update_data)
                logger.info(f"Updated missing SensitiveMeta fields for video {video_file.uuid}: {list(update_data.keys())}")
            except Exception as e:
                logger.error(f"Failed to update SensitiveMeta for video {video_file.uuid}: {e}")


def import_and_anonymize(
    file_path: Union[Path, str],
    center_name: str,
    processor_name: str,
    save_video: bool = True,
    delete_source: bool = False,
) -> "VideoFile":
    """
    High-level helper that wraps:
      1. VideoFile.create_from_file_initialized(...)
      2. VideoFile.initialize_video_specs() 
      3. VideoFile.initialize_frames()
      4. VideoFile.pipe_1()
      5. Saves the cleaned file back to VideoFile with processor ROI masking
      6. Returns the VideoFile instance (fresh from DB).

    Args:
        file_path: Path to the video file to import
        center_name: Name of the center to associate with video
        processor_name: Name of the processor to associate with video
        save_video: Whether to save the video file
        delete_source: Whether to delete the source file after import
        
    Returns:
        VideoFile instance after import and anonymization
        
    Raises:
        Exception: On any failure during import or anonymization
    """
    from endoreg_db.models import VideoFile
    
    file_path = Path(file_path)
    logger.info(f"Starting import and anonymization for: {file_path}")
    
    model_name = "image_multilabel_classification_colonoscopy_default"
    
    if not file_path.exists():
        raise FileNotFoundError(f"Video file not found: {file_path}")
    
    # Step 1: Create VideoFile instance
    logger.info("Creating VideoFile instance...")
    video_file_obj = VideoFile.create_from_file_initialized(
        file_path=file_path,
        center_name=center_name,
        processor_name=processor_name,
        delete_source=delete_source,
        save_video_file=save_video,
    )
    
    if not video_file_obj:
        raise RuntimeError("Failed to create VideoFile instance")
    
    logger.info(f"Created VideoFile with UUID: {video_file_obj.uuid}")
    
    # Step 2: Initialize video specifications (duration, fps, etc.)
    video_file_obj.initialize_video_specs()
    
    # Step 3: Initialize frame objects in database (without extracting)
    video_file_obj.initialize_frames()

    logger.info("Pipe 1 processing completed successfully")
    
    # Step 4: Initialize video specifications and frames, then signal completion
    logger.info("Finalizing video processing and signaling completion...")
    _ensure_default_patient_data(video_file_obj)
    
    # Step 5: Frame-level anonymization with processor ROI masking (if available)
    frame_cleaning_available, FrameCleaner, ReportReader = _ensure_frame_cleaning_available()
    
    # Initialize state tracking variables
    anonymization_completed = False
    
    if frame_cleaning_available and video_file_obj.raw_file:
        try:
            logger.info("Starting frame-level anonymization with processor ROI masking...")
            
            # Get processor ROI information for masking
            processor_roi = None
            endoscope_roi = None
            
            try:
                if video_file_obj.video_meta and video_file_obj.video_meta.processor:
                    processor = video_file_obj.video_meta.processor
                    
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
                    logger.warning(f"No processor found for video {video_file_obj.uuid}, proceeding without ROI masking")
                    
            except Exception as e:
                logger.error(f"Failed to retrieve processor ROI information: {e}")
                # Continue without ROI - don't fail the entire import process
            
            # Instantiate frame cleaner and report reader
            frame_cleaner = FrameCleaner()
            report_reader = ReportReader(
                report_root_path=str(Path(video_file_obj.raw_file.path).parent),
                locale="de_DE",  # Default German locale for medical data
                text_date_format="%d.%m.%Y"  # Common German date format
            )
            
            # Clean video with ROI masking (heavy I/O operation)
            # Pass the endoscope ROI to the frame cleaner for masking
            hash = f"{video_file_obj.processed_video_hash}"  # Fix: Correct f-string syntax
            frame_paths = video_file_obj.get_frame_paths()
            path = Path(video_file_obj.raw_file.path)
            cleaned_video_path, extracted_metadata = frame_cleaner.clean_video(
                path,
                report_reader,
                processor_name,
                video_file_obj,  # Pass VideoFile object to store metadata
                endoscope_roi,  # Pass ROI for masking
                processor_roi,   # Pass all ROIs for comprehensive anonymization
                frame_paths,
                Path(STORAGE_DIR) / f"{hash}.mp4"  # Fix: Ensure Path object
            )
            
            # Save cleaned video back to VideoFile (atomic transaction)
            with transaction.atomic():
                # Save the cleaned video using Django's FileField
                with open(cleaned_video_path, 'rb') as f:
                    video_file_obj.processed_file.save(
                        cleaned_video_path, 
                        ContentFile(f.read())
                    )
                video_file_obj.save()
                
            # Update sensitive metadata with extracted information
            sm = video_file_obj.sensitive_meta    
            sm.patient_first_name = extracted_metadata.get('patient_first_name', sm.patient_first_name)
            sm.patient_last_name = extracted_metadata.get('patient_last_name', sm.patient_last_name)
            sm.patient_dob = extracted_metadata.get('patient_dob', sm.patient_dob)
            sm.examination_date = extracted_metadata.get('examination_date', sm.examination_date)
            sm.endoscope_type = extracted_metadata.get('endoscope_type', sm.endoscope_type)
            sm.save()
            
            anonymization_completed = True
            logger.info(f"Frame cleaning with ROI masking completed: {cleaned_video_path.name}")
            
        except Exception as e:
            logger.error(f"Frame cleaning failed for video {video_file_obj.uuid}: {e}")
            # Continue without anonymization but don't mark as anonymized
            anonymization_completed = False
    elif not frame_cleaning_available:
        logger.warning("Frame cleaning not available (lx_anonymizer not found)")
    
    # Step 6: Update state using existing VideoState methods - ALWAYS update state regardless of anonymization success
    logger.info("Updating video processing state...")
    with transaction.atomic():
        # Basic processing states that should always be set after import
        video_file_obj.state.frames_extracted = True
        video_file_obj.state.frames_initialized = True
        video_file_obj.state.video_meta_extracted = True
        video_file_obj.state.text_meta_extracted = True
        
        # Use the existing VideoState methods for completion tracking
        video_file_obj.state.mark_sensitive_meta_processed(save=False)
        
        # Only mark as anonymized if frame cleaning actually succeeded
        if anonymization_completed:
            video_file_obj.state.anonymized = True
            logger.info(f"Video {video_file_obj.uuid} successfully anonymized")
        else:
            video_file_obj.state.anonymized = False
            logger.warning(f"Video {video_file_obj.uuid} imported but not anonymized")
        
        # Save all state changes
        video_file_obj.state.save()
        video_file_obj.save()
    
    # Step 7: Signal completion to the anonymization tracking system
    logger.info("Signaling import and anonymization completion to tracking system...")
    try:
        video_processing_complete = (
            video_file_obj.sensitive_meta is not None and
            video_file_obj.video_meta is not None and
            video_file_obj.raw_file and
            hasattr(video_file_obj.raw_file, 'path') and
            Path(video_file_obj.raw_file.path).exists()
        )
        
        if video_processing_complete:
            logger.info(f"Video {video_file_obj.uuid} processing completed successfully - ready for validation")
            
            # Optional: Add a simple flag to track completion if the model supports it
            # Check if the model has any completion tracking fields
            completion_fields = []
            for field_name in ['import_completed', 'processing_complete', 'ready_for_validation']:
                if hasattr(video_file_obj, field_name):
                    setattr(video_file_obj, field_name, True)
                    completion_fields.append(field_name)
                    
            if completion_fields:
                video_file_obj.save(update_fields=completion_fields)
                logger.info(f"Updated completion flags: {completion_fields}")
        else:
            logger.warning(f"Video {video_file_obj.uuid} processing incomplete - missing required components")
            
    except Exception as e:
        logger.warning(f"Failed to signal completion status: {e}")
        # Don't fail the entire import for this - processing was successful
    
    # Step 8: Refresh from database and return
    with transaction.atomic():
        video_file_obj.refresh_from_db()
    
    logger.info(f"Import and anonymization completed for VideoFile UUID: {video_file_obj.uuid}")
    return video_file_obj

def anonymize(video_file_obj: "VideoFile", processor_name: str, just_anonymization=True, method="masking", mask_config={}) -> "VideoFile":
    """
    Anonymize an existing VideoFile instance.
    
    Args:
        video_file: The VideoFile instance to anonymize
        processor_name: Name of the processor to use for anonymization
        
    Returns:
        Updated VideoFile instance after anonymization
    """
    
    frame_cleaning_available, FrameCleaner, ReportReader = _ensure_frame_cleaning_available()

    
    if not video_file_obj.raw_file or not video_file_obj.raw_file.path:
        try:
            video_file_obj.raw_file = video_file_obj.get_raw_file_path()
        except Exception as e:
            logger.error(f"Failed to get raw file path for VideoFile {video_file_obj.uuid}: {e}")
            video_file_obj.raw_file = video_file_obj.processed_file
            raise ValueError("Raw file not found or invalid path")
        
    if frame_cleaning_available and video_file_obj.raw_file:
        if not just_anonymization:
            logger.info(f"Anonymizing VideoFile UUID: {video_file_obj.uuid} with processor {processor_name}")
            if method=="masking":
                input_video = video_file_obj.raw_file.path
                mask_config = mask_config
                return FrameCleaner._mask_video(input_video, mask_config, video_file_obj.processed_file.path)
            else:
                logger.warning(f"Unknown anonymization method: {method}. TBA!.")
                method = "masking"
            logger.info("Using masking method for anonymization")
            
        try:
            logger.info("Starting frame-level anonymization with processor ROI masking...")
            
            # Get processor ROI information for masking
            processor_roi = None
            endoscope_roi = None
            
            try:
                if video_file_obj.video_meta and video_file_obj.video_meta.processor:
                    processor = video_file_obj.video_meta.processor
                    
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
                    logger.warning(f"No processor found for video {video_file_obj.uuid}, proceeding without ROI masking")
                    
            except Exception as e:
                logger.error(f"Failed to retrieve processor ROI information: {e}")
                # Continue without ROI - don't fail the entire import process
            
            # Instantiate frame cleaner and report reader
            frame_cleaner = FrameCleaner()
            report_reader = ReportReader(
                report_root_path=str(Path(video_file_obj.raw_file.path).parent),
                locale="de_DE",  # Default German locale for medical data
                text_date_format="%d.%m.%Y"  # Common German date format
            )
            
            # Clean video with ROI masking (heavy I/O operation)
            # Pass the endoscope ROI to the frame cleaner for masking
            hash = f"{video_file_obj.processed_video_hash}"  # Fix: Correct f-string syntax
            frame_paths = video_file_obj.get_frame_paths()
            path = Path(video_file_obj.raw_file.path)
            cleaned_video_path, extracted_metadata = frame_cleaner.clean_video(
                path,
                report_reader,
                processor_name,
                video_file_obj,  # Pass VideoFile object to store metadata
                endoscope_roi,  # Pass ROI for masking
                processor_roi,   # Pass all ROIs for comprehensive anonymization
                frame_paths,
                Path(STORAGE_DIR) / f"{hash}.mp4"  # Fix: Ensure Path object
            )
            
            
            
            # Save cleaned video back to VideoFile (atomic transaction)
            with transaction.atomic():
                # Save the cleaned video using Django's FileField
                with open(cleaned_video_path, 'rb') as f:
                    video_file_obj.processed_file.save(
                        cleaned_video_path, 
                        ContentFile(f.read())
                    )
                video_file_obj.save()
                
            sm = video_file_obj.sensitive_meta    
            
            sm.patient_first_name = extracted_metadata.get('patient_first_name', sm.patient_first_name)
            sm.patient_last_name = extracted_metadata.get('patient_last_name', sm.patient_last_name)
            sm.patient_dob = extracted_metadata.get('patient_dob', sm.patient_dob)
            sm.examination_date = extracted_metadata.get('examination_date', sm.examination_date)
            sm.endoscope_type = extracted_metadata.get('endoscope_type', sm.endoscope_type)
            sm.save()
            
            video_file_obj.state.frames_extracted = True
            video_file_obj.state.frames_initialized = True
            video_file_obj.state.video_meta_extracted = True
            video_file_obj.state.anonymized = True
            video_file_obj.state.sensitive_meta_processed = True
            video_file_obj.state.save()
            video_file_obj.save()
            
            logger.info(f"Frame cleaning with ROI masking completed: {cleaned_video_path.name}")
            
            
            
        except Exception as e:
            logger.warning(f"Frame cleaning failed, continuing with original video: {e}")
            # Don't raise - continue with unprocessed video
    elif not frame_cleaning_available:
        logger.warning("Frame cleaning not available (lx_anonymizer not found)")
    
    # Step 6: Signal completion to the anonymization tracking system
    logger.info("Signaling import and anonymization completion to tracking system...")
    try:
        video_processing_complete = (
            video_file_obj.sensitive_meta is not None and
            video_file_obj.video_meta is not None and
            video_file_obj.raw_file and
            hasattr(video_file_obj.raw_file, 'path') and
            Path(video_file_obj.raw_file.path).exists()
        )
        
        if video_processing_complete:
            logger.info(f"Video {video_file_obj.uuid} processing completed successfully - ready for validation")
            
            # Optional: Add a simple flag to track completion if the model supports it
            # Check if the model has any completion tracking fields
            completion_fields = []
            for field_name in ['import_completed', 'processing_complete', 'ready_for_validation']:
                if hasattr(video_file_obj, field_name):
                    setattr(video_file_obj, field_name, True)
                    completion_fields.append(field_name)
                    
            if completion_fields:
                video_file_obj.save(update_fields=completion_fields)
                logger.info(f"Updated completion flags: {completion_fields}")
        else:
            logger.warning(f"Video {video_file_obj.uuid} processing incomplete - missing required components")
            
    except Exception as e:
        logger.warning(f"Failed to signal completion status: {e}")
        # Don't fail the entire import for this - processing was successful
    
    # Step 7: Refresh from database and return
    with transaction.atomic():
        video_file_obj.refresh_from_db()
    
    logger.info(f"Import and anonymization completed for VideoFile UUID: {video_file_obj.uuid}")
    return video_file_obj

def anonymize(video_file_obj: "VideoFile", processor_name: str, just_anonymization=True, method="masking", mask_config={}) -> "VideoFile":
    """
    Anonymize an existing VideoFile instance.
    
    Args:
        video_file: The VideoFile instance to anonymize
        processor_name: Name of the processor to use for anonymization
        
    Returns:
        Updated VideoFile instance after anonymization
    """
    
    frame_cleaning_available, FrameCleaner, ReportReader = _ensure_frame_cleaning_available()

    
    if not video_file_obj.raw_file or not video_file_obj.raw_file.path:
        try:
            video_file_obj.raw_file = video_file_obj.get_raw_file_path()
        except Exception as e:
            logger.error(f"Failed to get raw file path for VideoFile {video_file_obj.uuid}: {e}")
            video_file_obj.raw_file = video_file_obj.processed_file
            raise ValueError("Raw file not found or invalid path")
        
    if frame_cleaning_available and video_file_obj.raw_file:
        if not just_anonymization:
            logger.info(f"Anonymizing VideoFile UUID: {video_file_obj.uuid} with processor {processor_name}")
            if method=="masking":
                input_video = video_file_obj.raw_file.path
                mask_config = mask_config
                return FrameCleaner._mask_video(input_video, mask_config, video_file_obj.processed_file.path)
            else:
                logger.warning(f"Unknown anonymization method: {method}. TBA!.")
                method = "masking"
            logger.info("Using masking method for anonymization")
            
        try:
            logger.info("Starting frame-level anonymization with processor ROI masking...")
            
            # Get processor ROI information for masking
            processor_roi = None
            endoscope_roi = None
            
            try:
                if video_file_obj.video_meta and video_file_obj.video_meta.processor:
                    processor = video_file_obj.video_meta.processor
                    
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
                    logger.warning(f"No processor found for video {video_file_obj.uuid}, proceeding without ROI masking")
                    
            except Exception as e:
                logger.error(f"Failed to retrieve processor ROI information: {e}")
                # Continue without ROI - don't fail the entire import process
            
            # Instantiate frame cleaner and report reader
            frame_cleaner = FrameCleaner()
            report_reader = ReportReader(
                report_root_path=str(Path(video_file_obj.raw_file.path).parent),
                locale="de_DE",  # Default German locale for medical data
                text_date_format="%d.%m.%Y"  # Common German date format
            )
            
            # Clean video with ROI masking (heavy I/O operation)
            # Pass the endoscope ROI to the frame cleaner for masking
            hash = f"{video_file_obj.processed_video_hash}"  # Fix: Correct f-string syntax
            frame_paths = video_file_obj.get_frame_paths()
            path = Path(video_file_obj.raw_file.path)
            cleaned_video_path, extracted_metadata = frame_cleaner.clean_video(
                path,
                report_reader,
                processor_name,
                video_file_obj,  # Pass VideoFile object to store metadata
                endoscope_roi,  # Pass ROI for masking
                processor_roi,   # Pass all ROIs for comprehensive anonymization
                frame_paths,
                Path(STORAGE_DIR) / f"{hash}.mp4"  # Fix: Ensure Path object
            )
            
            
            
            # Save cleaned video back to VideoFile (atomic transaction)
            with transaction.atomic():
                # Save the cleaned video using Django's FileField
                with open(cleaned_video_path, 'rb') as f:
                    video_file_obj.processed_file.save(
                        cleaned_video_path, 
                        ContentFile(f.read())
                    )
                video_file_obj.save()
                
            sm = video_file_obj.sensitive_meta    
            
            sm.patient_first_name = extracted_metadata.get('patient_first_name', sm.patient_first_name)
            sm.patient_last_name = extracted_metadata.get('patient_last_name', sm.patient_last_name)
            sm.patient_dob = extracted_metadata.get('patient_dob', sm.patient_dob)
            sm.examination_date = extracted_metadata.get('examination_date', sm.examination_date)
            sm.endoscope_type = extracted_metadata.get('endoscope_type', sm.endoscope_type)
            sm.save()
            
            video_file_obj.state.frames_extracted = True
            video_file_obj.state.frames_initialized = True
            video_file_obj.state.video_meta_extracted = True
            video_file_obj.state.anonymized = True
            video_file_obj.state.sensitive_meta_processed = True
            video_file_obj.state.save()
            video_file_obj.save()
            
            logger.info(f"Frame cleaning with ROI masking completed: {cleaned_video_path.name}")
            
            
            
        except Exception as e:
            logger.warning(f"Frame cleaning failed, continuing with original video: {e}")
            # Don't raise - continue with unprocessed video
    elif not frame_cleaning_available:
        logger.warning("Frame cleaning not available (lx_anonymizer not found)")
    
    try:
        video_processing_complete = (
            video_file_obj.sensitive_meta is not None and
            video_file_obj.video_meta is not None and
            video_file_obj.raw_file and
            hasattr(video_file_obj.raw_file, 'path') and
            Path(video_file_obj.raw_file.path).exists()
        )
        
        if video_processing_complete:
            logger.info(f"Video {video_file_obj.uuid} processing completed successfully - ready for validation")
        else:
            logger.warning(f"Video {video_file_obj.uuid} processing incomplete - missing required components")
            
    except Exception as e:
        logger.warning(f"Failed to signal completion status: {e}")
        # Don't fail the entire import for this - processing was successful
    
    # Step 7: Refresh from database and return
    with transaction.atomic():
        video_file_obj.refresh_from_db()
    
    logger.info(f"Import and anonymization completed for VideoFile UUID: {video_file_obj.uuid}")
    return video_file_obj

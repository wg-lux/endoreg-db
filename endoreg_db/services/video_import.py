"""
Video import service module.

Provides high-level functions for importing and anonymizing video files,
combining VideoFile creation with frame-level anonymization.
"""

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Union
from django.core.files.base import ContentFile
from django.db import transaction
from datetime import date

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
        endoreg_db_root = current_file.parent.parent.parent.parent
        lx_anonymizer_path = endoreg_db_root / "lx-anonymizer"
        
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
      4. VideoFile.pipe_1() - CRITICAL: This was missing!
      5. Saves the cleaned file back to VideoFile
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
    
    # Step 4: Run Pipe 1 - CRITICAL MISSING STEP!
    logger.info("Starting Pipe 1 processing...")
    success = video_file_obj.pipe_1(
        model_name=model_name,
        delete_frames_after=True,  # Clean up frames after processing
        ocr_frame_fraction=0.01,
        ocr_cap=5
    )
    
    if not success:
        raise RuntimeError(f"Pipe 1 processing failed for video {video_file_obj.uuid}")
    
    logger.info("Pipe 1 processing completed successfully")
    
    # Step 4.5: Ensure minimum patient data is available
    logger.info("Ensuring minimum patient data availability...")
    _ensure_default_patient_data(video_file_obj)
    
    # Step 5: Frame-level anonymization (if available)
    frame_cleaning_available, FrameCleaner, ReportReader = _ensure_frame_cleaning_available()
    
    if frame_cleaning_available and video_file_obj.raw_file:
        try:
            logger.info("Starting frame-level anonymization...")
            
            # Instantiate frame cleaner and report reader
            frame_cleaner = FrameCleaner()
            report_reader = ReportReader(
                report_root_path=str(video_file_obj.raw_file.path),
                locale="de_DE",  # Default German locale for medical data
                text_date_format="%d.%m.%Y"  # Common German date format
            )
            
            # Clean video (heavy I/O operation)
            cleaned_video_path = frame_cleaner.clean_video(
                Path(video_file_obj.raw_file.path),
                report_reader,
                device_name=processor_name
            )
            
            # Save cleaned video back to VideoFile (atomic transaction)
            with transaction.atomic():
                # Save the cleaned video using Django's FileField
                with open(cleaned_video_path, 'rb') as f:
                    video_file_obj.raw_file.save(
                        cleaned_video_path.name, 
                        ContentFile(f.read())
                    )
                video_file_obj.save()
                
            logger.info(f"Frame cleaning completed: {cleaned_video_path.name}")
            
        except Exception as e:
            logger.warning(f"Frame cleaning failed, continuing with original video: {e}")
            # Don't raise - continue with unprocessed video
    elif not frame_cleaning_available:
        logger.warning("Frame cleaning not available (lx_anonymizer not found)")
    
    # Step 6: Refresh from database and return
    with transaction.atomic():
        video_file_obj.refresh_from_db()
    
    logger.info(f"Import and anonymization completed for VideoFile UUID: {video_file_obj.uuid}")
    return video_file_obj
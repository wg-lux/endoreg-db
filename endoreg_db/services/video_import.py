"""
Video import service module.

Provides high-level functions for importing and anonymizing video files,
combining VideoFile creation with frame-level anonymization.
"""
from datetime import date
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Union
from django.core.files.base import ContentFile
from django.db import transaction
<<<<<<< HEAD
from endoreg_db.utils.paths import STORAGE_DIR, RAW_FRAME_DIR
from importlib import resources
from endoreg_db.models import VideoFile, SensitiveMeta
=======
from datetime import date
import os
from endoreg_db.models import VideoFile
>>>>>>> origin/prototype

try:
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


def _perform_anonymization(video_file_obj: "VideoFile", processor_name: str) -> "VideoFile":
    """
    Shared anonymization logic for VideoFile, including ROI retrieval, frame cleaning, and metadata updates.
    """
<<<<<<< HEAD
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
    
    # Step 4: Ensure default patient data BEFORE frame cleaning
    logger.info("Ensuring default patient data before frame cleaning...")
    _ensure_default_patient_data(video_file_obj)
    
    # Step 5: Frame-level anonymization with processor ROI masking (if available)
    frame_cleaning_available, FrameCleaner, ReportReader = _ensure_frame_cleaning_available()
    
    # Initialize state tracking variables
    anonymization_completed = False
    
=======
    frame_cleaning_available, FrameCleaner, ReportReader = _ensure_frame_cleaning_available()
    if not video_file_obj.raw_file or not getattr(video_file_obj.raw_file, 'path', None):
        try:
            video_file_obj.raw_file = video_file_obj.get_raw_file_path()
        except Exception as e:
            logger.error(f"Failed to get raw file path for VideoFile {video_file_obj.uuid}: {e}")
            video_file_obj.raw_file = video_file_obj.processed_file
            raise ValueError("Raw file not found or invalid path")

>>>>>>> origin/prototype
    if frame_cleaning_available and video_file_obj.raw_file:
        try:
            logger.info("Starting frame-level anonymization with processor ROI masking...")

            # Get processor ROI information for masking
            processor_roi = None
            endoscope_roi = None

            try:
                if video_file_obj.video_meta and video_file_obj.video_meta.processor:
                    processor = video_file_obj.video_meta.processor
<<<<<<< HEAD
                    video_meta = video_file_obj.video_meta
=======

>>>>>>> origin/prototype
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
<<<<<<< HEAD
            # Pass the endoscope ROI to the frame cleaner for masking
            hash = f"{video_file_obj.processed_video_hash}"  # Fix: Correct f-string syntax
=======
            hash = f"{video_file_obj.processed_video_hash}"
>>>>>>> origin/prototype
            frame_paths = video_file_obj.get_frame_paths()
            path = Path(video_file_obj.raw_file.path)
            device_name=processor.name
            tmp_dir=RAW_FRAME_DIR  
            cleaned_video_path, extracted_metadata = frame_cleaner.clean_video(
                path,
                video_file_obj,
                tmp_dir, 
                device_name,# Use default temp directory
                endoscope_roi,
                processor_roi, 
                Path(STORAGE_DIR) / f"{hash}.mp4"  # Fix: Ensure Path object
            )
<<<<<<< HEAD
            
=======

>>>>>>> origin/prototype
            # Save cleaned video back to VideoFile (atomic transaction)
            with transaction.atomic():
                # Save the cleaned video using Django's FileField
                with open(cleaned_video_path, 'rb') as f:
                    video_file_obj.processed_file.save(
                        cleaned_video_path, 
                        ContentFile(f.read())
                    )
                video_file_obj.save()
<<<<<<< HEAD
                
            # Update sensitive metadata with extracted information
            # Now we know sensitive_meta exists because we ensured it above
            sm = video_file_obj.sensitive_meta    
            if sm and extracted_metadata:
                sm.patient_first_name = extracted_metadata.get('patient_first_name', sm.patient_first_name)
                sm.patient_last_name = extracted_metadata.get('patient_last_name', sm.patient_last_name)
                sm.patient_dob = extracted_metadata.get('patient_dob', sm.patient_dob)
                sm.examination_date = extracted_metadata.get('examination_date', sm.examination_date)
                sm.endoscope_type = extracted_metadata.get('endoscope_type', sm.endoscope_type)
                sm.save()
                logger.info(f"Updated SensitiveMeta for video {video_file_obj.uuid} with extracted metadata")
            
            anonymization_completed = True
            logger.info(f"Frame cleaning with ROI masking completed: {cleaned_video_path.name}")
            
=======

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

            logger.info(f"Frame cleaning with ROI masking completed: {cleaned_video_path.name}")

>>>>>>> origin/prototype
        except Exception as e:
            logger.error(f"Frame cleaning failed for video {video_file_obj.uuid}: {e}")
            # Continue without anonymization but don't mark as anonymized
            anonymization_completed = False
    elif not frame_cleaning_available:
        logger.warning("Frame cleaning not available (lx_anonymizer not found)")
<<<<<<< HEAD
    
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
=======

    # Step 6: Signal completion to the anonymization tracking system
>>>>>>> origin/prototype
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
<<<<<<< HEAD
    
    # Step 8: Refresh from database and return
=======

    # Step 7: Refresh from database and return
>>>>>>> origin/prototype
    with transaction.atomic():
        video_file_obj.refresh_from_db()

    logger.info(f"Import and anonymization completed for VideoFile UUID: {video_file_obj.uuid}")
    return video_file_obj

def import_and_anonymize(
    file_path: Union[Path, str],
    center_name: str,
    processor_name: str,
    save_video: bool = True,
    ocr_frame_fraction=0.001,  # Default OCR frame fraction
    delete_source: bool = False,
) -> "VideoFile":
    """
    Imports a video file, initializes processing, and performs anonymization with frame-level masking.
    
    This function creates a `VideoFile` instance from the provided file, runs initial processing including OCR frame selection, ensures required patient metadata is present, and applies anonymization using processor-specific region-of-interest masking. The resulting `VideoFile` is updated with anonymized content and metadata.
    
    Parameters:
        file_path (Union[Path, str]): Path to the video file to import and anonymize.
        center_name (str): Name of the center associated with the video.
        processor_name (str): Name of the processor used for anonymization.
        save_video (bool, optional): Whether to save the imported video file. Defaults to True.
        ocr_frame_fraction (float, optional): Fraction of frames to use for OCR during processing. Defaults to 0.001.
        delete_source (bool, optional): Whether to delete the source file after import. Defaults to False.
    
    Returns:
        VideoFile: The imported and anonymized VideoFile instance.
    
    Raises:
        FileNotFoundError: If the specified video file does not exist.
        RuntimeError: If the VideoFile instance cannot be created.
        Exception: For other failures during import or anonymization.
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
    video_file_obj.pipe_1(
        model_name=model_name,
        ocr_frame_fraction = ocr_frame_fraction,
        test_run=True
    )

    logger.info("Pipe 1 processing completed successfully")
    
    # Step 4: Initialize video specifications and frames, then signal completion
    logger.info("Finalizing video processing and signaling completion...")
    _ensure_default_patient_data(video_file_obj)
    
    # Step 5: Frame-level anonymization and metadata update
    return _perform_anonymization(video_file_obj, processor_name)

def anonymize(video_file_obj: "VideoFile", processor_name: str, just_anonymization=True, method="masking", mask_config={}) -> "VideoFile":
    """
    Anonymize an existing VideoFile instance.
    
    Args:
        video_file: The VideoFile instance to anonymize
        processor_name: Name of the processor to use for anonymization
        
    Returns:
        Updated VideoFile instance after anonymization
    """
    if just_anonymization:
        return _perform_anonymization(video_file_obj, processor_name)
    else:
        frame_cleaning_available, FrameCleaner, ReportReader = _ensure_frame_cleaning_available()
        if frame_cleaning_available and video_file_obj.raw_file:
            logger.info(f"Anonymizing VideoFile UUID: {video_file_obj.uuid} with processor {processor_name}")
            if method == "masking":
                input_video = video_file_obj.raw_file.path
                return FrameCleaner._mask_video(input_video, mask_config, video_file_obj.processed_file.path)
            else:
                logger.warning(f"Unknown anonymization method: {method}. TBA!.")
<<<<<<< HEAD
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
=======
                logger.info("Using masking method for anonymization")
>>>>>>> origin/prototype
        else:
            logger.warning("Frame cleaning not available or raw file missing.")
        return video_file_obj

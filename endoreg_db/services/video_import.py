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
import os
import gc

try:
    STORAGE_DIR = os.getenv("STORAGE_DIR")
    if not STORAGE_DIR:
        STORAGE_DIR = Path(__file__).parent.parent.parent.parent / "data" / "videos"
    # Ensure STORAGE_DIR is a Path object
    STORAGE_DIR = Path(STORAGE_DIR)
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
except Exception as e:
    raise EnvironmentError(f"STORAGE_DIR setup failed: {e}") from e

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile

logger = logging.getLogger(__name__)

# Global cache for frame cleaning components to avoid re-initialization
_frame_cleaner_cache = None
_report_reader_cache = None

def _get_cached_frame_cleaner():
    """
    Get cached FrameCleaner instance to avoid model re-downloading.
    
    Returns:
        Cached FrameCleaner instance or None if not available
    """
    # Use global reference without global statement
    global _frame_cleaner_cache
    
    if _frame_cleaner_cache is None:
        try:
            frame_cleaning_available, FrameCleaner, _ = _ensure_frame_cleaning_available()
            
            if frame_cleaning_available:
                logger.info("Initializing cached FrameCleaner (one-time model download)...")
                
                # Configure FrameCleaner to avoid model re-downloads
                _frame_cleaner_cache = FrameCleaner(
                    use_minicpm=True,
                    minicpm_config={
                        'auto_cleanup': False,  # Don't cleanup during processing
                        'min_storage_gb': 10.0,  # Lower threshold for processing
                        'max_cache_size_gb': 150.0,  # Allow larger cache
                        'fallback_to_tesseract': True  # Enable fallback
                    }
                )
                
                logger.info("FrameCleaner cached successfully - subsequent calls will reuse this instance")
            else:
                logger.warning("Frame cleaning not available - caching disabled")
                
        except (ImportError, RuntimeError, OSError) as e:
            logger.error("Failed to cache FrameCleaner: %s", e)
            _frame_cleaner_cache = None
    
    return _frame_cleaner_cache

def _ensure_frame_cleaning_available():
    """
    Ensure frame cleaning modules are available by adding lx-anonymizer to path.
    
    Returns:
        Tuple of (availability_flag, FrameCleaner_class, ReportReader_class)
    """
    try:
        # Check if we can find the lx-anonymizer directory
        current_file = Path(__file__)
        project_root = current_file.parent.parent.parent
        lx_anonymizer_path = project_root / "lx-anonymizer"
        
        if lx_anonymizer_path.exists():
            # Add to Python path temporarily
            if str(lx_anonymizer_path) not in sys.path:
                sys.path.insert(0, str(lx_anonymizer_path))
            
            # Try simple import
            from lx_anonymizer.frame_cleaner import FrameCleaner
            from lx_anonymizer.report_reader import ReportReader
            
            logger.info("Successfully imported lx_anonymizer modules")
            
            return True, FrameCleaner, ReportReader
        
        else:
            logger.warning("lx-anonymizer path not found: %s", lx_anonymizer_path) 
            
    except (ImportError, ModuleNotFoundError, FileNotFoundError) as e:
        logger.warning("Frame cleaning not available: %s", e)
    
    return False, None, None

def _ensure_default_patient_data(video_file: "VideoFile") -> None:
    """
    Ensure video has minimum required patient data in SensitiveMeta.
    Creates default values if data is missing after OCR processing.
    """
    try:
        from endoreg_db.models import SensitiveMeta
        
        if not video_file.sensitive_meta:
            logger.info("No SensitiveMeta found for video %s, creating default", video_file.uuid)
            
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
                logger.info("Created default SensitiveMeta for video %s", video_file.uuid)
            except (ValueError, TypeError, AttributeError) as e:
                logger.error("Failed to create default SensitiveMeta for video %s: %s", video_file.uuid, e)
                
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
                    logger.info("Updated missing SensitiveMeta fields for video %s: %s", video_file.uuid, list(update_data.keys()))
                except (ValueError, TypeError, AttributeError) as e:
                    logger.error("Failed to update SensitiveMeta for video %s: %s", video_file.uuid, e)
                    
    except (ImportError, AttributeError) as e:
        logger.error("Error in _ensure_default_patient_data: %s", e)

def _check_storage_space(required_gb: float = 10.0) -> bool:
    """Check if there's enough storage space for processing."""
    try:
        import shutil
        _, _, free = shutil.disk_usage(STORAGE_DIR)
        free_gb = free // (1024**3)
        
        logger.info("Storage check: %sGB free, %sGB required", free_gb, required_gb)
        
        if free_gb < required_gb:
            logger.error("Insufficient storage: %sGB available, %sGB required", free_gb, required_gb)
            return False
        return True
    except (OSError, OverflowError) as e:
        logger.warning("Storage check failed: %s", e)
        return True  # Continue if check fails

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
      4. Frame-level anonymization with cached components
      5. Returns the VideoFile instance (fresh from DB).

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
    logger.info("Starting import and anonymization for: %s", file_path)
    
    # Check storage space before starting
    if not _check_storage_space(required_gb=10.0):
        raise RuntimeError("Insufficient storage space for video processing")
    
    if not file_path.exists():
        raise FileNotFoundError(f"Video file not found: {file_path}")
    
    try:
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
        
        logger.info("Created VideoFile with UUID: %s", video_file_obj.uuid)
        
        # Step 2: Initialize video specifications (duration, fps, etc.)
        logger.info("Initializing video specifications...")
        video_file_obj.initialize_video_specs()
        
        # Step 3: Initialize frame objects in database (without extracting)
        logger.info("Initializing frame objects...")
        video_file_obj.initialize_frames()

        logger.info("Pipe 1 processing completed successfully")
        
        # Step 4: Ensure default patient data
        logger.info("Ensuring default patient data...")
        _ensure_default_patient_data(video_file_obj)
        
        # Step 5: Frame-level anonymization with cached components
        logger.info("Starting frame-level anonymization...")
        
        # Use cached FrameCleaner to avoid model re-download
        frame_cleaner = _get_cached_frame_cleaner()
        
        if frame_cleaner is None:
            logger.warning("Frame cleaning not available - skipping anonymization")
        elif not video_file_obj.raw_file:
            logger.warning("No raw file available for anonymization")
        else:
            try:
                logger.info("Processing with cached FrameCleaner (no model re-download)...")
                
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
                        
                        logger.info("Retrieved processor ROI information: endoscope_roi=%s", endoscope_roi)
                        
                    else:
                        logger.warning("No processor found for video %s, proceeding without ROI masking", video_file_obj.uuid)
                        
                except (AttributeError, ValueError, TypeError) as e:
                    logger.error("Failed to retrieve processor ROI information: %s", e)
                    # Continue without ROI - don't fail the entire import process
                
                # Create report reader (lightweight operation)
                try:
                    from lx_anonymizer.report_reader import ReportReader
                    report_reader = ReportReader(
                        report_root_path=str(Path(video_file_obj.raw_file.path).parent),
                        locale="de_DE",  # Default German locale for medical data
                        text_date_format="%d.%m.%Y"  # Common German date format
                    )
                    logger.info("ReportReader created successfully")
                except (ImportError, ValueError, OSError) as e:
                    logger.error("Failed to create ReportReader: %s", e)
                    raise
                
                # Generate proper output path
                try:
                    # Fix: Ensure hash is not None
                    processed_hash = getattr(video_file_obj, 'processed_video_hash', None)
                    if processed_hash is None:
                        # Generate a temporary hash based on UUID if processed_video_hash is None
                        import hashlib
                        temp_hash = hashlib.md5(str(video_file_obj.uuid).encode()).hexdigest()[:8]
                        hash_val = f"f{temp_hash}"
                    else:
                        hash_val = f"f{processed_hash}"
                    
                    output_path = STORAGE_DIR / f"{hash_val}.mp4"
                    
                    # Ensure output directory exists
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    logger.info("Output path: %s", output_path)
                    
                except (OSError, ValueError) as e:
                    logger.error("Failed to generate output path: %s", e)
                    raise
                
                # Clean video with ROI masking (heavy I/O operation with progress tracking)
                try:
                    input_path = Path(video_file_obj.raw_file.path)
                    
                    logger.info("Starting video cleaning: %s -> %s", input_path, output_path)
                    
                    # Fix the parameter order and add missing parameters
                    cleaned_video_path, extracted_metadata = frame_cleaner.clean_video(
                        video_path=input_path,
                        video_file_obj=video_file_obj,  # Pass VideoFile object
                        report_reader=report_reader,    # Pass ReportReader object
                        tmp_dir=None,                   # Let it create temp dir
                        device_name=processor_name,     # Use processor name as device name
                        endoscope_roi=endoscope_roi,    # Pass endoscope ROI
                        processor_rois=processor_roi,   # Pass all processor ROIs
                        output_path=output_path         # Specify output path
                    )
                    
                    logger.info("Video cleaning completed: %s", cleaned_video_path)
                    
                except (RuntimeError, OSError, ValueError) as e:
                    logger.error("Video cleaning failed: %s", e)
                    raise

                # Save cleaned video back to VideoFile (atomic transaction)
                try:
                    with transaction.atomic():
                        logger.info("Saving cleaned video to database...")
                        
                        # Verify the cleaned video exists and is valid
                        if not cleaned_video_path.exists():
                            raise FileNotFoundError(f"Cleaned video not found: {cleaned_video_path}")
                        
                        file_size = cleaned_video_path.stat().st_size
                        if file_size == 0:
                            raise ValueError("Cleaned video file is empty")
                        
                        logger.info("Cleaned video size: %.1f MB", file_size / (1024*1024))
                        
                        # Save the cleaned video using Django's FileField
                        with open(cleaned_video_path, 'rb') as f:
                            video_file_obj.processed_file.save(
                                cleaned_video_path.name, 
                                ContentFile(f.read())
                            )
                        
                        logger.info("Cleaned video saved to database successfully")
                        
                except (OSError, ValueError, TypeError) as e:
                    logger.error("Failed to save cleaned video: %s", e)
                    # Add debugging info
                    logger.error("cleaned_video_path type: %s", type(cleaned_video_path))
                    logger.error("cleaned_video_path value: %s", cleaned_video_path)
                    raise
                
                # Update metadata
                try:
                    logger.info("Updating metadata...")
                    
                    sm = video_file_obj.sensitive_meta    
                    
                    sm.patient_first_name = extracted_metadata.get('patient_first_name', sm.patient_first_name)
                    sm.patient_last_name = extracted_metadata.get('patient_last_name', sm.patient_last_name)
                    sm.patient_dob = extracted_metadata.get('patient_dob', sm.patient_dob)
                    sm.examination_date = extracted_metadata.get('examination_date', sm.examination_date)
                    sm.endoscope_type = extracted_metadata.get('endoscope_type', sm.endoscope_type)
                    sm.save()
                    
                    # Update video state
                    video_file_obj.state.frames_extracted = True
                    video_file_obj.state.frames_initialized = True
                    video_file_obj.state.video_meta_extracted = True
                    video_file_obj.state.anonymized = True
                    video_file_obj.state.sensitive_meta_processed = True
                    
                    video_file_obj.save()
                    
                    logger.info("Metadata updated successfully")
                    
                except (AttributeError, ValueError, TypeError) as e:
                    logger.error("Failed to update metadata: %s", e)
                    raise
                
                # Cleanup temporary files
                try:
                    if cleaned_video_path != output_path and cleaned_video_path.exists():
                        cleaned_video_path.unlink()
                        logger.info("Cleaned up temporary file: %s", cleaned_video_path)
                except OSError as e:
                    logger.warning("Failed to cleanup temporary files: %s", e)
                
                logger.info("Frame cleaning with cached components completed successfully")
                
            except (RuntimeError, ValueError, OSError, ImportError) as e:
                logger.error("Frame cleaning failed: %s", e)
                # Mark the state as failed but don't raise to allow partial processing
                try:
                    video_file_obj.state.processing_error = True
                    video_file_obj.state.update_anonymization_status(save=True)
                except (AttributeError, ValueError):
                    pass
                # Re-raise for now - you might want to handle this differently
                raise
        
        # Step 6: Signal completion
        logger.info("Signaling completion...")
        try:
            video_processing_complete = (
                video_file_obj.sensitive_meta is not None and
                video_file_obj.video_meta is not None and
                video_file_obj.raw_file and
                hasattr(video_file_obj.raw_file, 'path') and
                Path(video_file_obj.raw_file.path).exists()
            )
            
            if video_processing_complete:
                logger.info("Video %s processing completed successfully - ready for validation", video_file_obj.uuid)
                
                # Update completion flags if they exist
                completion_fields = []
                for field_name in ['import_completed', 'processing_complete', 'ready_for_validation']:
                    if hasattr(video_file_obj, field_name):
                        setattr(video_file_obj, field_name, True)
                        completion_fields.append(field_name)
                        
                if completion_fields:
                    video_file_obj.save(update_fields=completion_fields)
                    logger.info("Updated completion flags: %s", completion_fields)
            else:
                logger.warning("Video %s processing incomplete - missing required components", video_file_obj.uuid)
                
        except (AttributeError, OSError) as e:
            logger.warning("Failed to signal completion status: %s", e)
            # Don't fail the entire import for this
        
        # Step 7: Final refresh and return
        try:
            with transaction.atomic():
                video_file_obj.refresh_from_db()
        except (AttributeError, ValueError) as e:
            logger.warning("Failed to refresh from database: %s", e)
        
        logger.info("Import and anonymization completed for VideoFile UUID: %s", video_file_obj.uuid)
        return video_file_obj
        
    except (RuntimeError, ValueError, FileNotFoundError, OSError) as e:
        logger.error("Import and anonymization failed for %s: %s", file_path, e)
        
        # Force garbage collection to free memory
        gc.collect()
        
        # Try to clear CUDA cache if available
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        
        raise

def clear_frame_cleaner_cache():
    """Clear the cached FrameCleaner instance to free memory."""
    global _frame_cleaner_cache, _report_reader_cache
    
    if _frame_cleaner_cache is not None:
        logger.info("Clearing FrameCleaner cache...")
        try:
            if hasattr(_frame_cleaner_cache, 'cleanup'):
                _frame_cleaner_cache.cleanup()
        except (AttributeError, RuntimeError) as e:
            logger.warning("Error during FrameCleaner cleanup: %s", e)
        
        _frame_cleaner_cache = None
    
    if _report_reader_cache is not None:
        logger.info("Clearing ReportReader cache...")
        _report_reader_cache = None
    
    # Force garbage collection
    gc.collect()
    
    # Clear CUDA cache if available
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            logger.info("Cleared CUDA cache")
    except ImportError:
        pass
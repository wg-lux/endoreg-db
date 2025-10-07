"""
Video import service module.

Provides high-level functions for importing and anonymizing video files,
combining VideoFile creation with frame-level anonymization.
"""
from datetime import date
import logging
import sys
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Union, Dict, Any, Optional
from django.db import transaction
from django.core.exceptions import FieldError
from endoreg_db.models import VideoFile, SensitiveMeta
from endoreg_db.utils.paths import STORAGE_DIR, RAW_FRAME_DIR, VIDEO_DIR, ANONYM_VIDEO_DIR
import random
from lx_anonymizer.ocr import trocr_full_image_ocr


class VideoImportService():
    """
    Service for importing and anonymizing video files.
    Uses a central video instance pattern for cleaner state management.
    """
    
    def __init__(self, project_root: Path = None):
        
        # Set up project root path
        if project_root:
            self.project_root = Path(project_root)
        else:
            self.project_root = Path(__file__).parent.parent.parent.parent
        
        # Track processed files to prevent duplicates
        self.processed_files = set(str(file) for file in os.listdir(ANONYM_VIDEO_DIR))
            
        self.STORAGE_DIR = STORAGE_DIR
        
        # Central video instance and processing context
        self.current_video = None
        self.processing_context: Dict[str, Any] = {}
        
        if TYPE_CHECKING:
            from endoreg_db.models import VideoFile

        self.logger = logging.getLogger(__name__)
    
    def processed(self) -> bool:
        """Indicates if the current file has already been processed."""
        return getattr(self, '_processed', False)
                
    def import_and_anonymize(
        self,
        file_path: Union[Path, str],
        center_name: str,
        processor_name: str,
        save_video: bool = True,
        delete_source: bool = True,
    ) -> "VideoFile":
        """
        High-level helper that orchestrates the complete video import and anonymization process.
        Uses the central video instance pattern for improved state management.

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
        try:
            # Initialize processing context
            self._initialize_processing_context(file_path, center_name, processor_name, 
                                               save_video, delete_source)
            
            # Validate and prepare file
            self._validate_and_prepare_file()
            
            # Create or retrieve video instance
            self._create_or_retrieve_video_instance()
            
            # Setup processing environment
            self._setup_processing_environment()
            
            # Process frames and metadata
            self._process_frames_and_metadata()
            
            # Finalize processing
            self._finalize_processing()
            
            # Move files and cleanup
            self._cleanup_and_archive()
            
            return self.current_video
            
        except Exception as e:
            self.logger.error(f"Video import and anonymization failed for {file_path}: {e}")
            self._cleanup_on_error()
            raise
        finally:
            self._cleanup_processing_context()

    def _initialize_processing_context(self, file_path: Union[Path, str], center_name: str, 
                                     processor_name: str, save_video: bool, delete_source: bool):
        """Initialize the processing context for the current video import."""
        self.processing_context = {
            'file_path': Path(file_path),
            'center_name': center_name,
            'processor_name': processor_name,
            'save_video': save_video,
            'delete_source': delete_source,
            'processing_started': False,
            'frames_extracted': False,
            'anonymization_completed': False,
            'error_reason': None
        }
        
        self.logger.info(f"Initialized processing context for: {file_path}")

    def _validate_and_prepare_file(self):
        """Validate the video file and prepare for processing."""
        file_path = self.processing_context['file_path']
        
        # Check if already processed
        if str(file_path) in self.processed_files:
            self.logger.info(f"File {file_path} already processed, skipping")
            self.processed = True
            raise ValueError(f"File already processed: {file_path}")
        
        # Check file exists
        if not file_path.exists():
            raise FileNotFoundError(f"Video file not found: {file_path}")
        
        self.logger.info(f"File validation completed for: {file_path}")

    def _create_or_retrieve_video_instance(self):
        """Create or retrieve the VideoFile instance and move to final storage."""
        from endoreg_db.models import VideoFile
        
        self.logger.info("Creating VideoFile instance...")
        
        self.current_video = VideoFile.create_from_file_initialized(
            file_path=self.processing_context['file_path'],
            center_name=self.processing_context['center_name'],
            processor_name=self.processing_context['processor_name'],
            delete_source=self.processing_context['delete_source'],
            save_video_file=self.processing_context['save_video'],
        )
        
        if not self.current_video:
            raise RuntimeError("Failed to create VideoFile instance")
        
        # Immediately move to final storage locations
        self._move_to_final_storage()
        
        self.logger.info(f"Created VideoFile with UUID: {self.current_video.uuid}")
        
        # Get and mark processing state
        state = VideoFile.get_or_create_state(self.current_video)
        if not state:
            raise RuntimeError("Failed to create VideoFile state")
        
        state.mark_processing_started(save=True)
        self.processing_context['processing_started'] = True

    def _move_to_final_storage(self):
        """
        Move video from raw_videos to final storage locations.
        - Raw video → /data/videos (raw_file_path) 
        - Processed video will later → /data/anonym_videos (file_path)
        """
        from endoreg_db.utils import data_paths
        
        source_path = self.processing_context['file_path']
        
        # Define target directories
        videos_dir = data_paths["video"]  # /data/videos for raw files
        videos_dir.mkdir(parents=True, exist_ok=True)
        
        # Create target path for raw video in /data/videos
        video_filename = f"{self.current_video.uuid}_{Path(source_path).name}"
        raw_target_path = videos_dir / video_filename
        
        # Move source file to raw video storage
        try:
            shutil.move(str(source_path), str(raw_target_path))
            self.logger.info(f"Moved raw video to: {raw_target_path}")
        except Exception as e:
            self.logger.error(f"Failed to move video to final storage: {e}")
            raise
        
        # Update the raw_file path in database (relative to storage root)
        try:
            storage_root = data_paths["storage"]
            relative_path = raw_target_path.relative_to(storage_root)
            self.current_video.raw_file.name = str(relative_path)
            self.current_video.save(update_fields=['raw_file'])
            self.logger.info(f"Updated raw_file path to: {relative_path}")
        except Exception as e:
            self.logger.error(f"Failed to update raw_file path: {e}")
            # Fallback to simple relative path
            self.current_video.raw_file.name = f"videos/{video_filename}"
            self.current_video.save(update_fields=['raw_file'])
            self.logger.info(f"Updated raw_file path using fallback: videos/{video_filename}")
            
        
        # Store paths for later processing
        self.processing_context['raw_video_path'] = raw_target_path
        self.processing_context['video_filename'] = video_filename

    def _setup_processing_environment(self):
        """Setup the processing environment without file movement."""
        # Ensure we have a valid video instance
        if not self.current_video:
            raise RuntimeError("No video instance available for processing environment setup")
        
        # Initialize video specifications
        self.current_video.initialize_video_specs()
        
        # Initialize frame objects in database
        self.current_video.initialize_frames()
        
        # Extract frames BEFORE processing to prevent pipeline 1 conflicts
        self.logger.info("Pre-extracting frames to avoid pipeline conflicts...")
        try:
            frames_extracted = self.current_video.extract_frames(overwrite=False)
            if frames_extracted:
                self.processing_context['frames_extracted'] = True
                self.logger.info("Frame extraction completed successfully")
                
                # CRITICAL: Immediately save the frames_extracted state to database
                # to prevent refresh_from_db() in pipeline 1 from overriding it
                state = self.current_video.get_or_create_state()
                if not state.frames_extracted:
                    state.frames_extracted = True
                    state.save(update_fields=['frames_extracted'])
                    self.logger.info("Persisted frames_extracted=True to database")
            else:
                self.logger.warning("Frame extraction failed, but continuing...")
                self.processing_context['frames_extracted'] = False
        except Exception as e:
            self.logger.warning(f"Frame extraction failed during setup: {e}, but continuing...")
            self.processing_context['frames_extracted'] = False
        
        # Ensure default patient data
        self._ensure_default_patient_data()
        
        self.logger.info("Processing environment setup completed")

    def _process_frames_and_metadata(self):
        """Process frames and extract metadata with anonymization."""
        # Check frame cleaning availability
        frame_cleaning_available, FrameCleaner, ReportReader = self._ensure_frame_cleaning_available()
        
        if not frame_cleaning_available:
            self.logger.warning("Frame cleaning not available (lx_anonymizer not found)")
            return
        
        if not (frame_cleaning_available and self.current_video.raw_file):
            self.logger.warning("Frame cleaning conditions not met")
            return
        
        try:
            self.logger.info("Starting frame-level anonymization with processor ROI masking...")
            
            # Get processor ROI information
            processor_roi, endoscope_roi = self._get_processor_roi_info()
            
            # Perform frame cleaning
            self._perform_frame_cleaning(FrameCleaner, processor_roi, endoscope_roi)
            
            self.processing_context['anonymization_completed'] = True
            
        except Exception as e:
            self.logger.warning(f"Frame cleaning failed, continuing with original video: {e}")
            self.processing_context['anonymization_completed'] = False
            self.processing_context['error_reason'] = f"Frame cleaning failed: {e}"
            

    def _finalize_processing(self):
        """Finalize processing and update video state."""
        self.logger.info("Updating video processing state...")
        
        with transaction.atomic():
            # Update basic processing states
            # Only mark frames as extracted if they were successfully extracted
            if self.processing_context.get('frames_extracted', False):
                self.current_video.state.frames_extracted = True
                self.logger.info("Marked frames as extracted in state")
            else:
                self.logger.warning("Frames were not extracted, not updating state")
                
            self.current_video.state.frames_initialized = True
            self.current_video.state.video_meta_extracted = True
            self.current_video.state.text_meta_extracted = True
            
            # Mark sensitive meta as processed
            self.current_video.state.mark_sensitive_meta_processed(save=False)
            
            # Update completion status based on anonymization success
            if self.processing_context['anonymization_completed']:
                self.logger.info(f"Video {self.current_video.uuid} successfully anonymized")
            else:
                self.logger.warning(f"Video {self.current_video.uuid} imported but not anonymized")
            
            # Save all state changes
            self.current_video.state.save()
            self.current_video.save()
        
        # Signal completion
        self._signal_completion()

    def _cleanup_and_archive(self):
        """Move processed video to anonym_videos and cleanup."""
        from endoreg_db.utils import data_paths
        
        # Define target directory for processed videos
        anonym_videos_dir = data_paths["anonym_video"]  # /data/anonym_videos
        anonym_videos_dir.mkdir(parents=True, exist_ok=True)
        
        # Check if we have a processed/cleaned video
        processed_video_path = None
        
        # Look for cleaned video from frame cleaning process
        if 'cleaned_video_path' in self.processing_context:
            processed_video_path = self.processing_context['cleaned_video_path']
        else:
            # If no processing occurred, copy from raw video location
            raw_video_path = self.processing_context.get('raw_video_path')
            if raw_video_path and Path(raw_video_path).exists():
                video_filename = self.processing_context.get('video_filename', Path(raw_video_path).name)
                processed_filename = f"processed_{video_filename}"
                processed_video_path = Path(raw_video_path).parent / processed_filename
                
                # Copy raw to processed location (will be moved to anonym_videos)
                try:
                    shutil.copy2(str(raw_video_path), str(processed_video_path))
                    self.logger.info(f"Copied raw video for processing: {processed_video_path}")
                except Exception as e:
                    self.logger.error(f"Failed to copy raw video: {e}")
                    processed_video_path = raw_video_path  # Use original as fallback
        
        # Move processed video to anonym_videos
        if processed_video_path and Path(processed_video_path).exists():
            try:
                anonym_video_filename = f"anonym_{self.processing_context.get('video_filename', Path(processed_video_path).name)}"
                anonym_target_path = anonym_videos_dir / anonym_video_filename
                
                shutil.move(str(processed_video_path), str(anonym_target_path))
                self.logger.info(f"Moved processed video to: {anonym_target_path}")
                
                storage_root = data_paths["storage"]
                relative_path = anonym_target_path.relative_to(storage_root)
                
                # Update the file field in database (for processed video)
                try:
                    self.current_video.processed_file = str(relative_path)
                    self.current_video.save(update_fields=['processed_file'])
                    self.logger.info(f"Updated file path to: {relative_path}")
                except FieldError:
                    self.logger.warning("Field 'processed_file' does not exist on VideoFile, skipping update")
                    raise FieldError                    
            except Exception as e:
                self.logger.error(f"Failed to move processed video to anonym_videos: {e}")
        
        # Cleanup temporary directories
        try:
            from endoreg_db.utils.paths import RAW_FRAME_DIR
            shutil.rmtree(RAW_FRAME_DIR, ignore_errors=True)
            self.logger.debug(f"Cleaned up temporary frames directory: {RAW_FRAME_DIR}")
        except Exception as e:
            self.logger.warning(f"Failed to remove directory {RAW_FRAME_DIR}: {e}")
        
        # Handle source file deletion - this should already be moved, but check raw_videos
        source_path = self.processing_context['file_path']
        if self.processing_context['delete_source'] and Path(source_path).exists():
            try:
                os.remove(source_path)
                self.logger.info(f"Removed remaining source file: {source_path}")
            except Exception as e:
                self.logger.warning(f"Failed to remove source file {source_path}: {e}")
        
        # Mark as processed
        self.processed_files.add(str(self.processing_context['file_path']))
        
        # Refresh from database and finalize state
        with transaction.atomic():
            self.current_video.refresh_from_db()
            if hasattr(self.current_video, 'state'):
                self.current_video.state.mark_sensitive_meta_processed(save=True)
        
        self.logger.info(f"Import and anonymization completed for VideoFile UUID: {self.current_video.uuid}")
        self.logger.info("Raw video stored in: /data/videos")
        self.logger.info("Processed video stored in: /data/anonym_videos")
    
    def _create_sensitive_file(self, video_instance: "VideoFile" = None, file_path: Union[Path, str] = None) -> Path:
        """
        Create a sensitive file for the given video file by copying the original file and updating the path.
        Uses the central video instance and processing context if parameters not provided.

        Args:
            video_instance: Optional video instance, defaults to self.current_video
            file_path: Optional file path, defaults to processing_context['file_path']

        Returns:
            Path: The path to the created sensitive file.
        """
        video_file = video_instance or self.current_video
        # Always use the currently stored raw file path from the model to avoid deleting external source assets
        source_path = None
        try:
            if video_file and hasattr(video_file, 'raw_file') and video_file.raw_file and hasattr(video_file.raw_file, 'path'):
                source_path = Path(video_file.raw_file.path)
        except Exception:
            source_path = None
        # Fallback only if explicitly provided (do NOT default to processing_context input file)
        if source_path is None and file_path is not None:
            source_path = Path(file_path)
        
        if not video_file:
            raise ValueError("No video instance available for creating sensitive file")
        if not source_path:
            raise ValueError("No file path available for creating sensitive file")
        
        if not video_file.raw_file:
            raise ValueError("VideoFile must have a raw_file to create a sensitive file")
        
        # Ensure the target directory exists
        target_dir = VIDEO_DIR / 'sensitive'
        if not target_dir.exists():
            self.logger.info(f"Creating sensitive file directory: {target_dir}")
            os.makedirs(target_dir, exist_ok=True)
        
        # Move the stored raw file into the sensitive directory within storage
        target_file_path = target_dir / source_path.name
        try:
            # Prefer a move within the storage to avoid extra disk usage. This does not touch external input files.
            shutil.move(str(source_path), str(target_file_path))
            self.logger.info(f"Moved raw file to sensitive directory: {target_file_path}")
        except Exception as e:
            # Fallback to copy if move fails (e.g., cross-device or permissions), then remove only the original stored raw file
            self.logger.warning(f"Failed to move raw file to sensitive dir, copying instead: {e}")
            shutil.copy(str(source_path), str(target_file_path))
            try:
                # Remove only the stored raw file copy; never touch external input paths here
                os.remove(source_path)
            except FileNotFoundError:
                pass
        
        # Update the model to point to the sensitive file location
        # Use relative path from storage root, like in create_from_file.py
        try:
            from endoreg_db.utils import data_paths
            storage_root = data_paths["storage"]
            relative_path = target_file_path.relative_to(storage_root)
            video_file.raw_file.name = str(relative_path)
            video_file.save(update_fields=['raw_file'])
            self.logger.info(f"Updated video.raw_file to point to sensitive location: {relative_path}")
        except Exception as e:
            # Fallback to absolute path conversion if relative path fails
            self.logger.warning(f"Failed to set relative path, using fallback: {e}")
            video_file.raw_file.name = f"videos/sensitive/{target_file_path.name}"
            video_file.save(update_fields=['raw_file'])
            self.logger.info(f"Updated video.raw_file using fallback method: videos/sensitive/{target_file_path.name}")
        
        # Important: Do NOT remove the original input asset passed to the service here.
        # Source file cleanup for external inputs is handled by create_from_file via delete_source flag.
        
        self.logger.info(f"Created sensitive file for {video_file.uuid} at {target_file_path}")
        return target_file_path




    def _ensure_frame_cleaning_available(self):
        """
        Ensure frame cleaning modules are available by adding lx-anonymizer to path.
        
        Returns:
            Tuple of (availability_flag, FrameCleaner_class, ReportReader_class)
        """
        try:
            # Check if we can find the lx-anonymizer directory
            from importlib import resources
            lx_anonymizer_path = resources.files("lx_anonymizer")
            
            if lx_anonymizer_path.exists():
                # Add to Python path temporarily
                if str(lx_anonymizer_path) not in sys.path:
                    sys.path.insert(0, str(lx_anonymizer_path))
                
                # Try simple import
                from lx_anonymizer import FrameCleaner, ReportReader
                
                self.logger.info("Successfully imported lx_anonymizer modules")
                
                # Remove from path to avoid conflicts
                if str(lx_anonymizer_path) in sys.path:
                    sys.path.remove(str(lx_anonymizer_path))
                    
                return True, FrameCleaner, ReportReader
            
            else:
                self.logger.warning(f"lx-anonymizer path not found: {lx_anonymizer_path}") 
                
        except Exception as e:
            self.logger.warning(f"Frame cleaning not available: {e}")
        
        return False, None, None

    def _get_processor_roi_info(self):
        """Get processor ROI information for masking."""
        processor_roi = None
        endoscope_roi = None
        
        try:
            if self.current_video.video_meta and self.current_video.video_meta.processor:
                processor = getattr(self.current_video.video_meta, "processor", None)
                
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
                
                self.logger.info(f"Retrieved processor ROI information: endoscope_roi={endoscope_roi}")
            else:
                self.logger.warning(f"No processor found for video {self.current_video.uuid}, proceeding without ROI masking")
                
        except Exception as e:
            self.logger.error(f"Failed to retrieve processor ROI information: {e}")
            # Continue without ROI - don't fail the entire import process
        
        return processor_roi, endoscope_roi


    def _ensure_default_patient_data(self, video_instance: "VideoFile" = None) -> None:
        """
        Ensure video has minimum required patient data in SensitiveMeta.
        Creates default values if data is missing after OCR processing.
        Uses the central video instance if parameter not provided.
        
        Args:
            video_instance: Optional video instance, defaults to self.current_video
        """
        video_file = video_instance or self.current_video
        
        if not video_file:
            raise ValueError("No video instance available for ensuring patient data")
            
        if not video_file.sensitive_meta:
            self.logger.info(f"No SensitiveMeta found for video {video_file.uuid}, creating default")
            
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
                
                # Mark sensitive meta as processed after creating default data
                state = video_file.get_or_create_state()
                state.mark_sensitive_meta_processed(save=True)
                
                self.logger.info(f"Created default SensitiveMeta for video {video_file.uuid}")
            except Exception as e:
                self.logger.error(f"Failed to create default SensitiveMeta for video {video_file.uuid}: {e}")
                return
                
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
                    
                    # Mark sensitive meta as processed after updating missing fields
                    state = video_file.get_or_create_state()
                    state.mark_sensitive_meta_processed(save=True)
                    
                    self.logger.info(f"Updated missing SensitiveMeta fields for video {video_file.uuid}: {list(update_data.keys())}")
                except Exception as e:
                    self.logger.error(f"Failed to update SensitiveMeta for video {video_file.uuid}: {e}")


    def _ensure_frame_cleaning_available(self):
        """
        Ensure frame cleaning modules are available by adding lx-anonymizer to path.
        
        Returns:
            Tuple of (availability_flag, FrameCleaner_class, ReportReader_class)
        """
        try:
            # Check if we can find the lx-anonymizer directory
            from importlib import resources
            lx_anonymizer_path = resources.files("lx_anonymizer")
            
            if lx_anonymizer_path.exists():
                # Add to Python path temporarily
                if str(lx_anonymizer_path) not in sys.path:
                    sys.path.insert(0, str(lx_anonymizer_path))
                
                # Try simple import
                from lx_anonymizer import FrameCleaner, ReportReader
                
                self.logger.info("Successfully imported lx_anonymizer modules")
                
                # Remove from path to avoid conflicts
                if str(lx_anonymizer_path) in sys.path:
                    sys.path.remove(str(lx_anonymizer_path))
                    
                return True, FrameCleaner, ReportReader
            
            else:
                self.logger.warning(f"lx-anonymizer path not found: {lx_anonymizer_path}") 
                
        except Exception as e:
            self.logger.warning(f"Frame cleaning not available: {e}")
        
        return False, None, None

    def _get_processor_roi_info(self):
        """Get processor ROI information for masking."""
        processor_roi = None
        endoscope_roi = None
        
        try:
            if self.current_video.video_meta and self.current_video.video_meta.processor:
                processor = getattr(self.current_video.video_meta, "processor", None)
                
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
                
                self.logger.info(f"Retrieved processor ROI information: endoscope_roi={endoscope_roi}")
            else:
                self.logger.warning(f"No processor found for video {self.current_video.uuid}, proceeding without ROI masking")
                
        except Exception as e:
            self.logger.error(f"Failed to retrieve processor ROI information: {e}")
            # Continue without ROI - don't fail the entire import process
        
        return processor_roi, endoscope_roi

    def _perform_frame_cleaning(self, FrameCleaner, processor_roi, endoscope_roi):
        """Perform frame cleaning and anonymization."""
        # Instantiate frame cleaner
        frame_cleaner = FrameCleaner()
        
        # Prepare parameters for frame cleaning
        raw_video_path = self.processing_context.get('raw_video_path')
        
        if not raw_video_path or not Path(raw_video_path).exists():
            raise RuntimeError(f"Raw video path not found: {raw_video_path}")
        
        # Get processor name safely
        processor = getattr(self.current_video.video_meta, "processor", None) if self.current_video.video_meta else None
        device_name = processor.name if processor else self.processing_context['processor_name']
        
        tmp_dir = RAW_FRAME_DIR
        
        # Create temporary output path for cleaned video
        video_filename = self.processing_context.get('video_filename', Path(raw_video_path).name)
        cleaned_filename = f"cleaned_{video_filename}"
        cleaned_video_path = Path(raw_video_path).parent / cleaned_filename
        
        # Clean video with ROI masking (heavy I/O operation)
        actual_cleaned_path, extracted_metadata = frame_cleaner.clean_video(
            Path(raw_video_path),
            self.current_video,
            tmp_dir,
            device_name,
            endoscope_roi,
            processor_roi,
            cleaned_video_path
        )
        
        # Optional: enrich metadata using TrOCR+LLM on one random extracted frame
        try:
            # Prefer frames belonging to this video (UUID in path), else pick any frame
            frame_candidates = list(RAW_FRAME_DIR.rglob("*.jpg")) + list(RAW_FRAME_DIR.rglob("*.png"))
            video_uuid = str(self.current_video.uuid)
            filtered = [p for p in frame_candidates if video_uuid in str(p)] or frame_candidates
            if filtered:
                sample_frame = random.choice(filtered)
                ocr_text = trocr_full_image_ocr(sample_frame)
                if ocr_text:
                    llm_metadata = frame_cleaner.extract_metadata(ocr_text)
                    if llm_metadata:
                        # Merge with already extracted frame-level metadata
                        extracted_metadata = frame_cleaner.frame_metadata_extractor.merge_metadata(
                            extracted_metadata or {}, llm_metadata
                        )
                        self.logger.info("LLM metadata extraction (random frame) successful")
                    else:
                        self.logger.info("LLM metadata extraction (random frame) found no data")
                else:
                    self.logger.info("No text extracted by TrOCR on random frame")
        except Exception as e:
            self.logger.error(f"LLM metadata enrichment step failed: {e}")
        
        # Store cleaned video path for later use in _cleanup_and_archive
        self.processing_context['cleaned_video_path'] = actual_cleaned_path
        self.processing_context['extracted_metadata'] = extracted_metadata
        
        # Update sensitive metadata with extracted information
        self._update_sensitive_metadata(extracted_metadata)
        self.logger.info(f"Extracted metadata from frame cleaning: {extracted_metadata}")
        
        self.logger.info(f"Frame cleaning with ROI masking completed: {actual_cleaned_path}")
        self.logger.info("Cleaned video will be moved to anonym_videos during cleanup")

    def _update_sensitive_metadata(self, extracted_metadata):
        """
        Update sensitive metadata with extracted information.
        
        SAFETY MECHANISM: Only updates fields that are empty, default values, or explicitly marked as safe to overwrite.
        This prevents accidentally overwriting valuable manually entered or previously extracted data.
        """
        if not (self.current_video.sensitive_meta and extracted_metadata):
            return
        
        sm = self.current_video.sensitive_meta
        updated_fields = []
        
        # Map extracted metadata to SensitiveMeta fields
        metadata_mapping = {
            'patient_first_name': 'patient_first_name',
            'patient_last_name': 'patient_last_name',
            'patient_dob': 'patient_dob',
            'examination_date': 'examination_date',
            'endoscope_type': 'endoscope_type'
        }
        
        # Define default/placeholder values that are safe to overwrite
        SAFE_TO_OVERWRITE_VALUES = [
            'Patient',           # Default first name
            'Unknown',           # Default last name
            date(1990, 1, 1),   # Default DOB
            None,               # Empty values
            '',                 # Empty strings
            'N/A',              # Placeholder values
            'Unknown Device',   # Default device name
        ]
        
        for meta_key, sm_field in metadata_mapping.items():
            if extracted_metadata.get(meta_key) and hasattr(sm, sm_field):
                old_value = getattr(sm, sm_field)
                new_value = extracted_metadata[meta_key]
                
                # Enhanced safety check: Only update if current value is safe to overwrite
                if new_value and (old_value in SAFE_TO_OVERWRITE_VALUES):
                    self.logger.info(f"Updating {sm_field} from '{old_value}' to '{new_value}' for video {self.current_video.uuid}")
                    setattr(sm, sm_field, new_value)
                    updated_fields.append(sm_field)
                elif new_value and old_value and old_value not in SAFE_TO_OVERWRITE_VALUES:
                    self.logger.info(f"Preserving existing {sm_field} value '{old_value}' (not overwriting with '{new_value}') for video {self.current_video.uuid}")
        
        if updated_fields:
            sm.save(update_fields=updated_fields)
            self.logger.info(f"Updated SensitiveMeta fields for video {self.current_video.uuid}: {updated_fields}")
            
            # Mark sensitive meta as processed after successful update
            self.current_video.state.mark_sensitive_meta_processed(save=True)
            self.logger.info(f"Marked sensitive metadata as processed for video {self.current_video.uuid}")
        else:
            self.logger.info(f"No SensitiveMeta fields updated for video {self.current_video.uuid} - all existing values preserved")

    def _signal_completion(self):
        """Signal completion to the tracking system."""
        try:
            video_processing_complete = (
                self.current_video.sensitive_meta is not None and
                self.current_video.video_meta is not None and
                self.current_video.raw_file and
                hasattr(self.current_video.raw_file, 'path') and
                Path(self.current_video.raw_file.path).exists()
            )
            
            if video_processing_complete:
                self.logger.info(f"Video {self.current_video.uuid} processing completed successfully - ready for validation")
                
                # Update completion flags if they exist
                completion_fields = []
                for field_name in ['import_completed', 'processing_complete', 'ready_for_validation']:
                    if hasattr(self.current_video, field_name):
                        setattr(self.current_video, field_name, True)
                        completion_fields.append(field_name)
                
                if completion_fields:
                    self.current_video.save(update_fields=completion_fields)
                    self.logger.info(f"Updated completion flags: {completion_fields}")
            else:
                self.logger.warning(f"Video {self.current_video.uuid} processing incomplete - missing required components")
                
        except Exception as e:
            self.logger.warning(f"Failed to signal completion status: {e}")

    def _cleanup_on_error(self):
        """Cleanup processing context on error."""
        if self.current_video and hasattr(self.current_video, 'state'):
            try:
                if self.processing_context.get('processing_started'):
                    self.current_video.state.frames_extracted = False
                    self.current_video.state.frames_initialized = False
                    self.current_video.state.video_meta_extracted = False
                    self.current_video.state.text_meta_extracted = False
                    self.current_video.state.save()
            except Exception as e:
                self.logger.warning(f"Error during cleanup: {e}")

    def _cleanup_processing_context(self):
        """Cleanup processing context."""
        try:
            # Clean up any temporary processing artifacts
            if self.processing_context.get('frames_extracted'):
                # Cleanup handled in _cleanup_and_archive
                pass
        except Exception as e:
            self.logger.warning(f"Error during context cleanup: {e}")
        finally:
            # Reset context
            self.current_video = None
            self.processing_context = {}

# Convenience function for callers/tests that expect a module-level import_and_anonymize
def import_and_anonymize(
    file_path,
    center_name: str,
    processor_name: str,
    save_video: bool = True,
    delete_source: bool = False,
) -> "VideoFile":
    """Module-level helper that instantiates VideoImportService and runs import_and_anonymize.
    Kept for backward compatibility with callers that import this function directly.
    """
    service = VideoImportService()
    return service.import_and_anonymize(
        file_path=file_path,
        center_name=center_name,
        processor_name=processor_name,
        save_video=save_video,
        delete_source=delete_source,
    )
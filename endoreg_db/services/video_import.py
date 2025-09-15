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
from typing import TYPE_CHECKING, Union
from django.db import transaction
from endoreg_db.models import VideoFile, SensitiveMeta
from endoreg_db.utils.paths import STORAGE_DIR, RAW_FRAME_DIR, VIDEO_DIR

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
        self.processed_files = set()
            
        self.STORAGE_DIR = STORAGE_DIR
        
        # Central video instance and processing context
        self.current_video = None
        self.processing_context = {}
        
        if TYPE_CHECKING:
            from endoreg_db.models import VideoFile

        self.logger = logging.getLogger(__name__)
                
    def import_and_anonymize(
        self,
        file_path: Union[Path, str],
        center_name: str,
        processor_name: str,
        save_video: bool = True,
        delete_source: bool = False,
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
            raise ValueError(f"File already processed: {file_path}")
        
        # Check file exists
        if not file_path.exists():
            raise FileNotFoundError(f"Video file not found: {file_path}")
        
        self.logger.info(f"File validation completed for: {file_path}")

    def _create_or_retrieve_video_instance(self):
        """Create or retrieve the VideoFile instance."""
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
        
        self.logger.info(f"Created VideoFile with UUID: {self.current_video.uuid}")
        
        # Get and mark processing state
        state = VideoFile.get_or_create_state(self.current_video)
        if not state:
            raise RuntimeError("Failed to create VideoFile state")
        
        state.mark_processing_started(save=True)
        self.processing_context['processing_started'] = True

    def _setup_processing_environment(self):
        """Setup the processing environment including sensitive file creation."""
        # Create sensitive file
        self._create_sensitive_file()
        
        # Initialize video specifications
        self.current_video.initialize_video_specs()
        
        # Initialize frame objects in database
        self.current_video.initialize_frames()
        self.processing_context['frames_extracted'] = True
        
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
            self.current_video.state.frames_extracted = True
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
        """Cleanup temporary files and move to final storage."""
        # Move processed files to storage
        try:
            metadata = self.processing_context.get('extracted_metadata', {})
            original_file_path = self.processing_context['file_path']
            
            if self.current_video.raw_file and hasattr(self.current_video.raw_file, 'path'):
                original_file_path = Path(self.current_video.raw_file.path)
                self._move_processed_files_to_storage(original_file_path, metadata)
        except Exception as e:
            self.logger.warning(f"Failed to move processed video files: {e}")
        
        # Cleanup temporary directories
        try:
            shutil.rmtree(RAW_FRAME_DIR, ignore_errors=True)
        except Exception as e:
            self.logger.warning(f"Failed to remove directory {RAW_FRAME_DIR}: {e}")
        
        # Handle source file deletion
        if self.processing_context['delete_source']:
            try:
                file_path = self.processing_context['file_path']
                os.remove(file_path)
                self.logger.info(f"Removed source file {file_path}")
            except Exception as e:
                self.logger.warning(f"Failed to remove file {file_path}: {e}")
        
        # Mark as processed
        self.processed_files.add(str(self.processing_context['file_path']))
        
        # Refresh from database
        with transaction.atomic():
            self.current_video.refresh_from_db()
        self.current_video.state.mark_sensitive_meta_processed(save=True)
        
        self.logger.info(f"Import and anonymization completed for VideoFile UUID: {self.current_video.uuid}")
    
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
            if video_file and video_file.raw_file and hasattr(video_file.raw_file, 'path'):
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
        video_file.raw_file = str(target_file_path)
        video_file.save(update_fields=['raw_file'])
        
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
                    self.logger.info(f"Updated missing SensitiveMeta fields for video {video_file.uuid}: {list(update_data.keys())}")
                except Exception as e:
                    self.logger.error(f"Failed to update SensitiveMeta for video {video_file.uuid}: {e}")
                    

    def _move_processed_files_to_storage(self, original_file_path: Union[Path, str] = None, metadata: dict = None):
        """
        Move processed video files from raw_videos to appropriate storage directories.
        Uses the central video instance and processing context if parameters not provided.
        
        Args:
            original_file_path: Optional original file path, defaults to processing_context['file_path']
            metadata: Optional processing metadata, defaults to processing_context['extracted_metadata']
        """
        if not self.current_video:
            raise ValueError("No video instance available for moving files")
        
        file_path = Path(original_file_path) if original_file_path else self.processing_context.get('file_path')
        metadata = metadata or self.processing_context.get('extracted_metadata', {})
        
        if not file_path:
            raise ValueError("No file path available for moving files")
        
        try:
            # Define target directories
            videos_dir = self.project_root / 'data' / 'videos'
            anonymized_videos_dir = self.project_root / 'data' / 'videos' / 'anonymized'
            frames_target = videos_dir / 'frames'
            
            # Create target directories
            videos_dir.mkdir(parents=True, exist_ok=True)
            anonymized_videos_dir.mkdir(parents=True, exist_ok=True)
            frames_target.mkdir(parents=True, exist_ok=True)
            
            file_path = Path(file_path)
            video_name = file_path.stem
            
            # 1. Move the original processed video to videos directory
            if file_path.exists():
                target_video_path = videos_dir / f"{video_name}{file_path.suffix}"
                shutil.move(str(file_path), str(target_video_path))
                self.logger.info(f"Moved original video to: {target_video_path}")
            
            # 2. Move anonymized video if it exists
            for suffix in ['.mp4', '.avi', '.mov']:  # Common video formats
                anonymized_video_path = file_path.parent / f"{video_name}_anonymized{suffix}"
                if anonymized_video_path.exists():
                    target_anonymized_path = anonymized_videos_dir / f"{video_name}_anonymized{suffix}"
                    shutil.move(str(anonymized_video_path), str(target_anonymized_path))
                    self.logger.info(f"Moved anonymized video to: {target_anonymized_path}")
                    break
            
            # 3. Move extracted frames if they exist
            frames_source = file_path.parent / 'frames'
            if frames_source.exists():
                # Create video-specific frame directory
                video_frames_target = frames_target / video_name
                video_frames_target.mkdir(parents=True, exist_ok=True)
                
                for frame_file in frames_source.glob(f"{video_name}*"):
                    target_frame_path = video_frames_target / frame_file.name
                    shutil.move(str(frame_file), str(target_frame_path))
                    self.logger.info(f"Moved frame to: {target_frame_path}")
                
                # Remove empty frames directory if it's empty
                try:
                    if not any(frames_source.iterdir()):
                        frames_source.rmdir()
                        self.logger.debug(f"Removed empty directory: {frames_source}")
                except OSError:
                    pass  # Directory not empty, that's fine
            
            # 4. Move any metadata files (e.g., .json, .xml)
            for metadata_file in file_path.parent.glob(f"{video_name}.*"):
                if metadata_file.suffix.lower() in ['.json', '.xml', '.txt', '.meta']:
                    target_metadata_path = videos_dir / metadata_file.name
                    if metadata_file.exists():
                        shutil.move(str(metadata_file), str(target_metadata_path))
                        self.logger.info(f"Moved metadata file to: {target_metadata_path}")
            
            self.logger.info(f"Successfully moved all processed files for {video_name}")
            
            # Clean up original file if it still exists
            if file_path.exists():
                os.remove(file_path)
                self.logger.info(f"Removed original file after moving: {file_path}")
            
        except Exception as e:
            self.logger.error(f"Error moving processed video files: {e}")
            raise

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
        hash = f"{self.current_video.processed_video_hash}"
        path = Path(self.current_video.raw_file.path)
        
        # Get processor name safely
        processor = getattr(self.current_video.video_meta, "processor", None) if self.current_video.video_meta else None
        device_name = processor.name if processor else self.processing_context['processor_name']
        
        tmp_dir = RAW_FRAME_DIR
        output_path = Path(self.STORAGE_DIR) / f"{hash}.mp4"
        
        # Clean video with ROI masking (heavy I/O operation)
        cleaned_video_path, extracted_metadata = frame_cleaner.clean_video(
            path,
            self.current_video,
            tmp_dir,
            device_name,
            endoscope_roi,
            processor_roi,
            output_path
        )
        
        # Save cleaned video back to VideoFile (atomic transaction)
        with transaction.atomic():
            # Save the cleaned video using Django's FileField
            from django.core.files.base import ContentFile
            with open(cleaned_video_path, 'rb') as f:
                self.current_video.processed_file.save(
                    cleaned_video_path.name,
                    ContentFile(f.read())
                )
            self.current_video.save()
        
        # Update sensitive metadata with extracted information
        self._update_sensitive_metadata(extracted_metadata)
        
        # Store metadata in processing context
        self.processing_context['extracted_metadata'] = extracted_metadata
        
        self.logger.info(f"Frame cleaning with ROI masking completed: {cleaned_video_path.name}")

    def _update_sensitive_metadata(self, extracted_metadata):
        """Update sensitive metadata with extracted information."""
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
        
        for meta_key, sm_field in metadata_mapping.items():
            if extracted_metadata.get(meta_key) and hasattr(sm, sm_field):
                old_value = getattr(sm, sm_field)
                new_value = extracted_metadata[meta_key]
                
                # Only update if new value is better than current
                if new_value and (not old_value or old_value in ['Patient', 'Unknown']):
                    setattr(sm, sm_field, new_value)
                    updated_fields.append(sm_field)
        
        if updated_fields:
            sm.save(update_fields=updated_fields)
            self.logger.info(f"Updated SensitiveMeta fields for video {self.current_video.uuid}: {updated_fields}")

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
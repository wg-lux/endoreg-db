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
import os
from endoreg_db.models import VideoFile, SensitiveMeta
from endoreg_db.utils.paths import STORAGE_DIR, RAW_FRAME_DIR
from importlib import resources

class VideoImportService():
    """
    Service for importing and anonymizing video files.
    """
    def __init__(self, project_root: Path = None):
        
        # Set up project root path
        if project_root:
            self.project_root = Path(project_root)
        else:
            self.project_root = Path(__file__).parent.parent.parent.parent
        
        # Track processed files to prevent duplicates
        self.processed_files = set()

        try:
            STORAGE_DIR = os.getenv("STORAGE_DIR")
            if not STORAGE_DIR:
                STORAGE_DIR = self.project_root / "data" / "videos"    
        except KeyError:
            raise EnvironmentError("STORAGE_DIR environment variable not set and default path not found.")
            
            
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
        
        # Check if the file has already been processed
        if str(file_path) in self.processed_files:
            self.logger.info(f"File {file_path} already processed, skipping")
            return None
        
        self.logger.info(f"Starting import and anonymization for: {file_path}")
        
        if not file_path.exists():
            raise FileNotFoundError(f"Video file not found: {file_path}")
        
        # Mark the file as processed early to prevent duplicates
        self.processed_files.add(str(file_path))
        
        self.logger.info("Creating VideoFile instance...")
        self.logger.info("Creating VideoFile instance...")
        video_file_obj = VideoFile.create_from_file_initialized(
            file_path=file_path,
            center_name=center_name,
            processor_name=processor_name,
            delete_source=delete_source,
            save_video_file=save_video,
        )
        
        if not video_file_obj:
            raise RuntimeError("Failed to create VideoFile instance")
        self.logger.info(f"Created VideoFile with UUID: {video_file_obj.uuid}")
        self.logger.info(f"Created VideoFile with UUID: {video_file_obj.uuid}")
        
        # Step 2: Initialize video specifications (duration, fps, etc.)
        video_file_obj.initialize_video_specs()
        
        # Step 3: Initialize frame objects in database (without extracting)
        video_file_obj.initialize_frames()
        self.logger.info("Pipe 1 processing completed successfully")
        self.logger.info("Pipe 1 processing completed successfully")
        
        self.logger.info("Ensuring default patient data before frame cleaning...")
        self._ensure_default_patient_data(video_file_obj)
        self._ensure_default_patient_data(video_file_obj)
        
        # Step 5: Frame-level anonymization with processor ROI masking (if available)
        frame_cleaning_available, FrameCleaner, ReportReader = self._ensure_frame_cleaning_available()
        
        # Initialize state tracking variables
        anonymization_completed = False
        
        if frame_cleaning_available and video_file_obj.raw_file:
            try:
                self.logger.info("Starting frame-level anonymization with processor ROI masking...")
                
                # Get processor ROI information for masking
                processor_roi = None
                endoscope_roi = None
                
                try:
                    if video_file_obj.video_meta and video_file_obj.video_meta.processor:
                        processor = video_file_obj.video_meta.processor
                        video_meta = video_file_obj.video_meta
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
                        self.logger.warning(f"No processor found for video {video_file_obj.uuid}, proceeding without ROI masking")
                        
                except Exception as e:
                    self.logger.error(f"Failed to retrieve processor ROI information: {e}")
                    # Continue without ROI - don't fail the entire import process
                
                # Instantiate frame cleaner and report reader
                frame_cleaner = FrameCleaner()

                
                # Clean video with ROI masking (heavy I/O operation)
                # Pass the endoscope ROI to the frame cleaner for masking
                hash = f"{video_file_obj.processed_video_hash}"  # Fix: Correct f-string syntax
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
                # Now we know sensitive_meta exists because we ensured it above
                sm = video_file_obj.sensitive_meta    
                if sm and extracted_metadata:
                    sm.patient_first_name = extracted_metadata.get('patient_first_name', sm.patient_first_name)
                    sm.patient_last_name = extracted_metadata.get('patient_last_name', sm.patient_last_name)
                    sm.patient_dob = extracted_metadata.get('patient_dob', sm.patient_dob)
                    sm.examination_date = extracted_metadata.get('examination_date', sm.examination_date)
                    sm.endoscope_type = extracted_metadata.get('endoscope_type', sm.endoscope_type)
                    self.logger.info(f"Updated SensitiveMeta for video {video_file_obj.uuid} with extracted metadata")
                    self.logger.info(f"Updated SensitiveMeta for video {video_file_obj.uuid} with extracted metadata")
                
                anonymization_completed = True
                self.logger.info(f"Frame cleaning with ROI masking completed: {cleaned_video_path.name}")
                
                self.logger.error(f"Frame cleaning failed for video {video_file_obj.uuid}: {e}")
                self.logger.error(f"Frame cleaning failed for video {video_file_obj.uuid}: {e}")
                # Continue without anonymization but don't mark as anonymized
                anonymization_completed = False
            except Exception as e:
                self.logger.warning(f"Frame cleaning failed, continuing with original video: {e}")
                # Don't raise - continue with unprocessed video
                anonymization_completed = False
                self.logger.warning(f"Frame cleaning failed for video {video_file_obj.uuid}: {e}")
                self.logger.warning(f"Frame cleaning failed for video {video_file_obj.uuid}: {e}")
        elif not frame_cleaning_available:
            self.logger.warning("Frame cleaning not available (lx_anonymizer not found)")
        
        self.logger.info("Updating video processing state...")
        self.logger.info("Updating video processing state...")
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
                self.logger.info(f"Video {video_file_obj.uuid} successfully anonymized")
                self.logger.info(f"Video {video_file_obj.uuid} successfully anonymized")
            else:
                self.logger.warning(f"Video {video_file_obj.uuid} imported but not anonymized")
                self.logger.warning(f"Video {video_file_obj.uuid} imported but not anonymized")
            
            # Save all state changes
            video_file_obj.state.save()
            video_file_obj.save()
        
        # Step 7: Signal completion to the anonymization tracking system
        self.logger.info("Signaling import and anonymization completion to tracking system...")
        try:
            video_processing_complete = (
                video_file_obj.sensitive_meta is not None and
                video_file_obj.video_meta is not None and
                video_file_obj.raw_file and
                hasattr(video_file_obj.raw_file, 'path') and
                Path(video_file_obj.raw_file.path).exists()
            )
            
            if video_processing_complete:
                self.logger.info(f"Video {video_file_obj.uuid} processing completed successfully - ready for validation")
                
                # Optional: Add a simple flag to track completion if the model supports it
                # Check if the model has any completion tracking fields
                completion_fields = []
                for field_name in ['import_completed', 'processing_complete', 'ready_for_validation']:
                    if hasattr(video_file_obj, field_name):
                        setattr(video_file_obj, field_name, True)
                        completion_fields.append(field_name)
                        
                if completion_fields:
                    video_file_obj.save(update_fields=completion_fields)
                    self.logger.info(f"Updated completion flags: {completion_fields}")
            else:
                self.logger.warning(f"Video {video_file_obj.uuid} processing incomplete - missing required components")
                
        except Exception as e:
            self.logger.warning(f"Failed to signal completion status: {e}")
            # Don't fail the entire import for this - processing was successful
        
        # Step 8: Move processed files to correct directories
        self.logger.info("Moving processed video files to target directories...")
        try:
            # Get video metadata if available
            metadata = {}
            if 'extracted_metadata' in locals():
                metadata = extracted_metadata
            
            # Get the original file path from the video file object
            if video_file_obj.raw_file and hasattr(video_file_obj.raw_file, 'path'):
                original_file_path = Path(video_file_obj.raw_file.path)
                self._move_processed_files_to_storage(original_file_path, video_file_obj, metadata)
        except Exception as e:
            self.logger.warning(f"Failed to move processed video files: {e}")
        
        # Step 9: Refresh from database and return
        with transaction.atomic():
            video_file_obj.refresh_from_db()
        
        self.logger.info(f"Import and anonymization completed for VideoFile UUID: {video_file_obj.uuid}")
        return video_file_obj


    def _ensure_frame_cleaning_available(self):
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


    def _ensure_default_patient_data(self, video_file: "VideoFile") -> None:
        """
        Ensure video has minimum required patient data in SensitiveMeta.
        Creates default values if data is missing after OCR processing.
        """
        from endoreg_db.models import SensitiveMeta
        
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

            if video_file.sensitive_meta is None:
            # --- create and attach ---
                video_file.sensitive_meta = sensitive_meta
                video_file.save(update_fields=["sensitive_meta"])
                self.logger.info("Created default SensitiveMeta for %s", video_file.uuid)
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

    def _move_processed_files_to_storage(self, original_file_path: Path, video_file: "VideoFile", metadata: dict):
        """
        Move processed video files from raw_videos to appropriate storage directories.
        
        Args:
            original_file_path: Original file path in raw_videos
            video_file: VideoFile instance
            metadata: Processing metadata
        """
        import shutil
        
        try:
            # Define target directories
            videos_dir = self.project_root / 'data' / 'videos'
            anonymized_videos_dir = self.project_root / 'data' / 'videos' / 'anonymized'
            frames_target = videos_dir / 'frames'
            
            # Create target directories
            videos_dir.mkdir(parents=True, exist_ok=True)
            anonymized_videos_dir.mkdir(parents=True, exist_ok=True)
            frames_target.mkdir(parents=True, exist_ok=True)
            
            original_file_path = Path(original_file_path)
            video_name = original_file_path.stem
            
            # 1. Move the original processed video to videos directory
            if original_file_path.exists():
                target_video_path = videos_dir / f"{video_name}{original_file_path.suffix}"
                shutil.move(str(original_file_path), str(target_video_path))
                self.logger.info(f"Moved original video to: {target_video_path}")
            
            # 2. Move anonymized video if it exists
            for suffix in ['.mp4', '.avi', '.mov']:  # Common video formats
                anonymized_video_path = original_file_path.parent / f"{video_name}_anonymized{suffix}"
                if anonymized_video_path.exists():
                    target_anonymized_path = anonymized_videos_dir / f"{video_name}_anonymized{suffix}"
                    shutil.move(str(anonymized_video_path), str(target_anonymized_path))
                    self.logger.info(f"Moved anonymized video to: {target_anonymized_path}")
                    break
            
            # 3. Move extracted frames if they exist
            frames_source = original_file_path.parent / 'frames'
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
            for metadata_file in original_file_path.parent.glob(f"{video_name}.*"):
                if metadata_file.suffix.lower() in ['.json', '.xml', '.txt', '.meta']:
                    target_metadata_path = videos_dir / metadata_file.name
                    if metadata_file.exists():
                        shutil.move(str(metadata_file), str(target_metadata_path))
                        self.logger.info(f"Moved metadata file to: {target_metadata_path}")
            
            self.logger.info(f"Successfully moved all processed files for {video_name}")
            
        except Exception as e:
            self.logger.error(f"Error moving processed video files: {e}")
            raise


    def _perform_anonymization(self, video_file_obj: "VideoFile", processor_name: str) -> "VideoFile":
        """
        Shared anonymization logic for VideoFile, including ROI retrieval, frame cleaning, and metadata updates.
        """
        frame_cleaning_available, FrameCleaner, ReportReader = self._ensure_frame_cleaning_available()
        if not video_file_obj.raw_file or not getattr(video_file_obj.raw_file, 'path', None):
            try:
                video_file_obj.raw_file = video_file_obj.get_raw_file_path()
            except Exception as e:
                self.logger.error(f"Failed to get raw file path for VideoFile {video_file_obj.uuid}: {e}")
                video_file_obj.raw_file = video_file_obj.processed_file
                raise ValueError("Raw file not found or invalid path")

        if frame_cleaning_available and video_file_obj.raw_file:
            try:
                self.logger.info("Starting frame-level anonymization with processor ROI masking...")

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

                        self.logger.info(f"Retrieved processor ROI information: endoscope_roi={endoscope_roi}")

                    else:
                        self.logger.warning(f"No processor found for video {video_file_obj.uuid}, proceeding without ROI masking")

                except Exception as e:
                    self.logger.error(f"Failed to retrieve processor ROI information: {e}")
                    # Continue without ROI - don't fail the entire import process

                # Instantiate frame cleaner and report reader
                frame_cleaner = FrameCleaner()
                report_reader = ReportReader(
                    report_root_path=str(Path(video_file_obj.raw_file.path).parent),
                    locale="de_DE",  # Default German locale for medical data
                    text_date_format="%d.%m.%Y"  # Common German date format
                )

                # Clean video with ROI masking (heavy I/O operation)
                hash = f"{video_file_obj.processed_video_hash}"
                frame_paths = video_file_obj.get_frame_paths()
                path = Path(video_file_obj.raw_file.path)
                cleaned_video_path, extracted_metadata = frame_cleaner.clean_video(
                    path,
                    video_file_obj,
                    RAW_FRAME_DIR,  # Use default temp directory
                    processor_name,
                    endoscope_roi,
                    processor_roi,
                    Path(STORAGE_DIR) / f"{hash}.mp4"
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

                self.logger.info(f"Frame cleaning with ROI masking completed: {cleaned_video_path.name}")

            except Exception as e:
                self.logger.warning(f"Frame cleaning failed, continuing with original video: {e}")
                # Don't raise - continue with unprocessed video
        elif not frame_cleaning_available:
            self.logger.warning("Frame cleaning not available (lx_anonymizer not found)")

        # Step 6: Signal completion to the anonymization tracking system
        self.logger.info("Signaling import and anonymization completion to tracking system...")
        try:
            video_processing_complete = (
                video_file_obj.sensitive_meta is not None and
                video_file_obj.video_meta is not None and
                video_file_obj.raw_file and
                hasattr(video_file_obj.raw_file, 'path') and
                Path(video_file_obj.raw_file.path).exists()
            )

            if video_processing_complete:
                self.logger.info(f"Video {video_file_obj.uuid} processing completed successfully - ready for validation")

                # Optional: Add a simple flag to track completion if the model supports it
                # Check if the model has any completion tracking fields
                completion_fields = []
                for field_name in ['import_completed', 'processing_complete', 'ready_for_validation']:
                    if hasattr(video_file_obj, field_name):
                        setattr(video_file_obj, field_name, True)
                        completion_fields.append(field_name)

                if completion_fields:
                    video_file_obj.save(update_fields=completion_fields)
                    self.logger.info(f"Updated completion flags: {completion_fields}")
            else:
                self.logger.warning(f"Video {video_file_obj.uuid} processing incomplete - missing required components")

        except Exception as e:
            self.logger.warning(f"Failed to signal completion status: {e}")
            # Don't fail the entire import for this - processing was successful

        # Step 7: Refresh from database and return
        with transaction.atomic():
            video_file_obj.refresh_from_db()

        self.logger.info(f"Import and anonymization completed for VideoFile UUID: {video_file_obj.uuid}")
        return video_file_obj

    def anonymize(self, video_file_obj: "VideoFile", processor_name: str, just_anonymization=True, method="masking", mask_config={}) -> "VideoFile":
        """
        Anonymize an existing VideoFile instance.
        
        Args:
            video_file: The VideoFile instance to anonymize
            processor_name: Name of the processor to use for anonymization
            
        Returns:
            Updated VideoFile instance after anonymization
        """
        
        frame_cleaning_available, FrameCleaner, ReportReader = self._ensure_frame_cleaning_available()

        
        if not video_file_obj.raw_file or not video_file_obj.raw_file.path:
            try:
                video_file_obj.raw_file = video_file_obj.get_raw_file_path()
            except Exception as e:
                self.logger.error(f"Failed to get raw file path for VideoFile {video_file_obj.uuid}: {e}")
                video_file_obj.raw_file = video_file_obj.processed_file
                raise ValueError("Raw file not found or invalid path")
            
            if not just_anonymization:
                self.logger.info(f"Anonymizing VideoFile UUID: {video_file_obj.uuid} with processor {processor_name}")
                if method=="masking":
                    input_video = video_file_obj.raw_file.path
                    mask_config = mask_config
                    return FrameCleaner._mask_video(input_video, mask_config, video_file_obj.processed_file.path)
                else:
                    self.logger.warning(f"Unknown anonymization method: {method}. TBA!.")
                    method = "masking"
                self.logger.info("Using masking method for anonymization")
                self.logger.info("Using masking method for anonymization")
                
            try:
                self.logger.info("Starting frame-level anonymization with processor ROI masking...")
                
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
                        
                        self.logger.info(f"Retrieved processor ROI information: endoscope_roi={endoscope_roi}")
                        
                    else:
                        self.logger.warning(f"No processor found for video {video_file_obj.uuid}, proceeding without ROI masking")
                        
                except Exception as e:
                    self.logger.error(f"Failed to retrieve processor ROI information: {e}")
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
                
                self.logger.info(f"Frame cleaning with ROI masking completed: {cleaned_video_path.name}")
                
                
                
            except Exception as e:
                self.logger.warning(f"Frame cleaning failed, continuing with original video: {e}")
                # Don't raise - continue with unprocessed video
        elif not frame_cleaning_available:
            self.logger.warning("Frame cleaning not available (lx_anonymizer not found)")
        
        try:
            video_processing_complete = (
                video_file_obj.sensitive_meta is not None and
                video_file_obj.video_meta is not None and
                video_file_obj.raw_file and
                hasattr(video_file_obj.raw_file, 'path') and
                Path(video_file_obj.raw_file.path).exists()
            )
            
            if video_processing_complete:
                self.logger.info(f"Video {video_file_obj.uuid} processing completed successfully - ready for validation")
            else:
                self.logger.warning(f"Video {video_file_obj.uuid} processing incomplete - missing required components")
                
        except Exception as e:
            self.logger.warning(f"Failed to signal completion status: {e}")
            # Don't fail the entire import for this - processing was successful
        
        # Move processed files to correct directories
        self.logger.info("Moving processed video files to target directories...")
        try:
            # Get video metadata if available
            metadata = {}
            if 'extracted_metadata' in locals():
                metadata = extracted_metadata
            
            # Get the original file path from the video file object
            if video_file_obj.raw_file and hasattr(video_file_obj.raw_file, 'path'):
                original_file_path = Path(video_file_obj.raw_file.path)
                self._move_processed_files_to_storage(original_file_path, video_file_obj, metadata)
        except Exception as e:
            self.logger.warning(f"Failed to move processed video files: {e}")
        
        # Step 7: Refresh from database and return
        with transaction.atomic():
            video_file_obj.refresh_from_db()
        
        self.logger.info(f"Import and anonymization completed for VideoFile UUID: {video_file_obj.uuid}")
        return video_file_obj

"""
Video import service module.

Provides high-level functions for importing and anonymizing video files,
combining VideoFile creation with frame-level anonymization.

Changelog:
    October 14, 2025: Added file locking mechanism to prevent race conditions
                      during concurrent video imports (matches PDF import pattern)
"""
from datetime import date
import logging
import sys
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Union, Dict, Any, Optional, List, Tuple
from django.db import transaction
from endoreg_db.models import VideoFile, SensitiveMeta
from endoreg_db.utils.paths import STORAGE_DIR, RAW_FRAME_DIR, VIDEO_DIR, ANONYM_VIDEO_DIR
import random
from lx_anonymizer.ocr import trocr_full_image_ocr
from endoreg_db.utils.hashs import get_video_hash
from endoreg_db.models.media.video.video_file_anonymize import _cleanup_raw_assets, _anonymize
from typing import TYPE_CHECKING
from django.db.models.fields.files import FieldFile

if TYPE_CHECKING:
    from endoreg_db.models import EndoscopyProcessor

# File lock configuration (matches PDF import)
STALE_LOCK_SECONDS = 6000  # 100 minutes - reclaim locks older than this
MAX_LOCK_WAIT_SECONDS = 90  # New: wait up to 90s for a non-stale lock to clear before skipping

logger = logging.getLogger(__name__)


class VideoImportService():
    """
    Service for importing and anonymizing video files.
    Uses a central video instance pattern for cleaner state management.
    
    Features (October 14, 2025):
        - File locking to prevent concurrent processing of the same video
        - Stale lock detection and reclamation (600s timeout)
        - Hash-based duplicate detection
        - Graceful fallback processing without lx_anonymizer
    """
    
    def __init__(self, project_root: Optional[Path] = None):
        
        # Set up project root path
        if project_root:
            self.project_root = Path(project_root)
        else:
            self.project_root = Path(__file__).parent.parent.parent.parent
        
        # Track processed files to prevent duplicates
        self.processed_files = set(str(file) for file in os.listdir(ANONYM_VIDEO_DIR))
            
        self.STORAGE_DIR = STORAGE_DIR
        
        # Central video instance and processing context
        self.current_video: Optional[VideoFile] = None
        self.processing_context: Dict[str, Any] = {}
        
        self.delete_source = False
        
        self.logger = logging.getLogger(__name__)

    def _require_current_video(self) -> VideoFile:
        """Return the current VideoFile or raise if it has not been initialized."""
        if self.current_video is None:
            raise RuntimeError("Current video instance is not set")
        return self.current_video
    
    @contextmanager
    def _file_lock(self, path: Path):
        """
        Create a file lock to prevent duplicate processing of the same video.
        
        This context manager creates a .lock file alongside the video file.
        If the lock file already exists, it checks if it's stale (older than
        STALE_LOCK_SECONDS) and reclaims it if necessary. If it's not stale,
        we now WAIT (up to MAX_LOCK_WAIT_SECONDS) instead of failing immediately.
        """
        lock_path = Path(str(path) + ".lock")
        fd = None
        try:
            deadline = time.time() + MAX_LOCK_WAIT_SECONDS
            while True:
                try:
                    # Atomic create; fail if exists
                    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                    break  # acquired
                except FileExistsError:
                    # Check for stale lock
                    age = None
                    try:
                        st = os.stat(lock_path)
                        age = time.time() - st.st_mtime
                    except FileNotFoundError:
                        # Race: lock removed between exists and stat; retry acquire in next loop
                        age = None
                    
                    if age is not None and age > STALE_LOCK_SECONDS:
                        try:
                            logger.warning(
                                "Stale lock detected for %s (age %.0fs). Reclaiming lock...",
                                path, age
                            )
                            lock_path.unlink()
                        except Exception as e:
                            logger.warning("Failed to remove stale lock %s: %s", lock_path, e)
                        # Loop continues and retries acquire immediately
                        continue
                    
                    # Not stale: wait until deadline, then give up gracefully
                    if time.time() >= deadline:
                        raise ValueError(f"File already being processed: {path}")
                    time.sleep(1.0)
            
            os.write(fd, b"lock")
            os.close(fd)
            fd = None
            yield
        finally:
            try:
                if fd is not None:
                    os.close(fd)
                if lock_path.exists():
                    lock_path.unlink()
            except OSError:
                pass
    
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
    ) -> "VideoFile|None":
        """
        High-level helper that orchestrates the complete video import and anonymization process.
        Uses the central video instance pattern for improved state management.
        """
        try:
            # Initialize processing context
            self._initialize_processing_context(file_path, center_name, processor_name, 
                                               save_video, delete_source)
            
            # Validate and prepare file (may raise ValueError if another worker holds a non-stale lock)
            try:
                self._validate_and_prepare_file()
            except ValueError as ve:
                # Relaxed behavior: if another process is working on this file, skip cleanly
                if "already being processed" in str(ve):
                    self.logger.info(f"Skipping {file_path}: {ve}")
                    return None
                raise
            
            # Create sensitive meta file, ensure raw is moved out of processing folder watched by file watcher.
            self._create_sensitive_file()
            
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
        """
        Validate the video file and prepare for processing.
        
        Uses file locking to prevent concurrent processing of the same video file.
        This prevents race conditions where multiple workers might try to process
        the same video simultaneously.
        
        The lock is acquired here and held for the entire import process.
        See _file_lock() for lock reclamation logic.
        """
        file_path = self.processing_context['file_path']
        
        # Acquire file lock to prevent concurrent processing
        # Lock will be held until finally block in import_and_anonymize()
        self.processing_context['_lock_context'] = self._file_lock(file_path)
        self.processing_context['_lock_context'].__enter__()
        
        self.logger.info("Acquired file lock for: %s", file_path)
        
        # Check if already processed (memory-based check)
        if str(file_path) in self.processed_files:
            self.logger.info("File %s already processed, skipping", file_path)
            self._processed = True
            raise ValueError(f"File already processed: {file_path}")
        
        # Check file exists
        if not file_path.exists():
            raise FileNotFoundError(f"Video file not found: {file_path}")
        
        self.logger.info("File validation completed for: %s", file_path)

    def _create_or_retrieve_video_instance(self):
        """Create or retrieve the VideoFile instance and move to final storage."""
        # Removed duplicate import of VideoFile (already imported at module level)
        
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
        
        self.logger.info("Created VideoFile with UUID: %s", self.current_video.uuid)
        
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

        videos_dir = data_paths["video"]
        videos_dir.mkdir(parents=True, exist_ok=True)

        _current_video = self.current_video
        assert _current_video is not None, "Current video instance is None during storage move"

        stored_raw_path = None
        if hasattr(_current_video, "get_raw_file_path"):
            possible_path = _current_video.get_raw_file_path()
            if possible_path:
                try:
                    stored_raw_path = Path(possible_path)
                except (TypeError, ValueError):
                    stored_raw_path = None

        if stored_raw_path:
            try:
                storage_root = data_paths["storage"]
                if stored_raw_path.is_absolute():
                    if not stored_raw_path.is_relative_to(storage_root):
                        stored_raw_path = None
                else:
                    if stored_raw_path.parts and stored_raw_path.parts[0] == videos_dir.name:
                        stored_raw_path = storage_root / stored_raw_path
                    else:
                        stored_raw_path = videos_dir / stored_raw_path.name
            except Exception:
                stored_raw_path = None

        if stored_raw_path and not stored_raw_path.suffix:
            stored_raw_path = None

        if not stored_raw_path:
            uuid_str = getattr(_current_video, "uuid", None)
            source_suffix = Path(source_path).suffix or ".mp4"
            filename = f"{uuid_str}{source_suffix}" if uuid_str else Path(source_path).name
            stored_raw_path = videos_dir / filename

        delete_source = bool(self.processing_context.get('delete_source'))
        stored_raw_path.parent.mkdir(parents=True, exist_ok=True)

        if not stored_raw_path.exists():
            try:
                if source_path.exists():
                    if delete_source:
                        shutil.move(str(source_path), str(stored_raw_path))
                        self.logger.info("Moved raw video to: %s", stored_raw_path)
                    else:
                        shutil.copy2(str(source_path), str(stored_raw_path))
                        self.logger.info("Copied raw video to: %s", stored_raw_path)
                else:
                    raise FileNotFoundError(f"Neither stored raw path nor source path exists for {self.processing_context['file_path']}")
            except Exception as e:
                self.logger.error("Failed to place video in final storage: %s", e)
                raise
        else:
            # If we already have the stored copy, respect delete_source flag without touching assets unnecessarily
            if delete_source and source_path.exists():
                try:
                    os.remove(source_path)
                    self.logger.info("Removed original source file after storing copy: %s", source_path)
                except OSError as e:
                    self.logger.warning("Failed to remove source file %s: %s", source_path, e)

        # Ensure database path points to stored location (relative to storage root)
        try:
            storage_root = data_paths["storage"]
            relative_path = Path(stored_raw_path).relative_to(storage_root)
            if _current_video.raw_file.name != str(relative_path):
                _current_video.raw_file.name = str(relative_path)
                _current_video.save(update_fields=['raw_file'])
                self.logger.info("Updated raw_file path to: %s", relative_path)
        except Exception as e:
            self.logger.error("Failed to ensure raw_file path is relative: %s", e)
            fallback_relative = Path("videos") / Path(stored_raw_path).name
            if _current_video.raw_file.name != fallback_relative.as_posix():
                _current_video.raw_file.name = fallback_relative.as_posix()
                _current_video.save(update_fields=['raw_file'])
                self.logger.info("Updated raw_file path using fallback: %s", fallback_relative.as_posix())

        # Store paths for later processing
        self.processing_context['raw_video_path'] = Path(stored_raw_path)
        self.processing_context['video_filename'] = Path(stored_raw_path).name

    def _setup_processing_environment(self):
        """Setup the processing environment without file movement."""
        video = self._require_current_video()

        # Initialize video specifications
        video.initialize_video_specs()

        # Initialize frame objects in database
        video.initialize_frames()
        
        # Extract frames BEFORE processing to prevent pipeline 1 conflicts
        self.logger.info("Pre-extracting frames to avoid pipeline conflicts...")
        try:
            frames_extracted = video.extract_frames(overwrite=False)
            if frames_extracted:
                self.processing_context['frames_extracted'] = True
                self.logger.info("Frame extraction completed successfully")
                
                # CRITICAL: Immediately save the frames_extracted state to database
                # to prevent refresh_from_db() in pipeline 1 from overriding it
                state = video.get_or_create_state()
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
        self._ensure_default_patient_data(video_instance=video)
        
        self.logger.info("Processing environment setup completed")

    def _process_frames_and_metadata(self):
        """Process frames and extract metadata with anonymization."""
        # Check frame cleaning availability
        frame_cleaning_available, FrameCleaner, ReportReader = self._ensure_frame_cleaning_available()
        video = self._require_current_video()

        raw_file_field = video.raw_file
        has_raw_file = isinstance(raw_file_field, FieldFile) and bool(raw_file_field.name)

        if not (frame_cleaning_available and has_raw_file):
            self.logger.warning("Frame cleaning not available or conditions not met, using fallback anonymization.")
            self._fallback_anonymize_video()
            return

        try:
            self.logger.info("Starting frame-level anonymization with processor ROI masking...")
            
            # Get processor ROI information
            endoscope_data_roi_nested, endoscope_image_roi = self._get_processor_roi_info()
            
            # Perform frame cleaning with timeout to prevent blocking
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
            
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self._perform_frame_cleaning, FrameCleaner, endoscope_data_roi_nested, endoscope_image_roi)
                try:
                    # Increased timeout to better accommodate ffmpeg + OCR
                    future.result(timeout=300)
                    self.processing_context['anonymization_completed'] = True
                    self.logger.info("Frame cleaning completed successfully within timeout")
                except FutureTimeoutError:
                    self.logger.warning("Frame cleaning timed out; entering grace period check for cleaned output")
                    # Grace period: detect if cleaned file appears shortly after timeout
                    raw_video_path = self.processing_context.get('raw_video_path')
                    video_filename = self.processing_context.get('video_filename', Path(raw_video_path).name if raw_video_path else "video.mp4")
                    grace_seconds = 60
                    expected_cleaned_path: Optional[Path] = None
                    processed_field = video.processed_file
                    if isinstance(processed_field, FieldFile) and processed_field.name:
                        try:
                            expected_cleaned_path = Path(processed_field.path)
                        except (NotImplementedError, TypeError, ValueError):
                            expected_cleaned_path = None
                    found = False
                    if expected_cleaned_path is not None:
                        for _ in range(grace_seconds):
                            if expected_cleaned_path.exists():
                                self.processing_context['cleaned_video_path'] = expected_cleaned_path
                                self.processing_context['anonymization_completed'] = True
                                self.logger.info("Detected cleaned video during grace period: %s", expected_cleaned_path)
                                found = True
                                break
                            time.sleep(1)
                    else:
                        self._fallback_anonymize_video()
                    if not found:
                        raise TimeoutError("Frame cleaning operation timed out - likely Ollama connection issue")

        except Exception as e:
            self.logger.warning("Frame cleaning failed (reason: %s), falling back to simple copy", e)
            # Try fallback anonymization when frame cleaning fails
            try:
                self._fallback_anonymize_video()
            except Exception as fallback_error:
                self.logger.error("Fallback anonymization also failed: %s", fallback_error)
                # If even fallback fails, mark as not anonymized but continue import
                self.processing_context['anonymization_completed'] = False
                self.processing_context['error_reason'] = f"Frame cleaning failed: {e}, Fallback failed: {fallback_error}"

    def _save_anonymized_video(self):
        video = self._require_current_video()
        anonymized_video_path = video.get_target_anonymized_video_path()

        if not anonymized_video_path.exists():
            raise RuntimeError(f"Processed video file not found after assembly for {video.uuid}: {anonymized_video_path}")

        new_processed_hash = get_video_hash(anonymized_video_path)
        if video.__class__.objects.filter(processed_video_hash=new_processed_hash).exclude(pk=video.pk).exists():
            raise ValueError(
                f"Processed video hash {new_processed_hash} already exists for another video (Video: {video.uuid})."
            )

        video.processed_video_hash = new_processed_hash
        video.processed_file.name = anonymized_video_path.relative_to(STORAGE_DIR).as_posix()

        update_fields = [
            "processed_video_hash",
            "processed_file",
            "frame_dir",
        ]

        if self.delete_source:
            original_raw_file_path_to_delete = video.get_raw_file_path()
            original_raw_frame_dir_to_delete = video.get_frame_dir_path()

            video.raw_file.name = None  # type: ignore[assignment]

            update_fields.extend(["raw_file", "video_hash"])

            transaction.on_commit(lambda: _cleanup_raw_assets(
                video_uuid=video.uuid,
                raw_file_path=original_raw_file_path_to_delete,
                raw_frame_dir=original_raw_frame_dir_to_delete
            ))

        video.save(update_fields=update_fields)
        video.state.mark_anonymized(save=True)
        video.refresh_from_db()
        self.current_video = video
        return True

    def _fallback_anonymize_video(self):
        """
        Fallback to create anonymized video if lx_anonymizer is not available.
        """
        try:
            self.logger.info("Attempting fallback video anonymization...")
            video = self.current_video
            if video is None:
                self.logger.warning("No VideoFile instance available for fallback anonymization")
            else:
                # Try VideoFile.pipe_2() method if available
                if hasattr(video, 'pipe_2'):
                    self.logger.info("Trying VideoFile.pipe_2() method...")
                    if video.pipe_2():
                        self.logger.info("VideoFile.pipe_2() succeeded")
                        self.processing_context['anonymization_completed'] = True
                        return
                    self.logger.warning("VideoFile.pipe_2() returned False")
                # Try direct anonymization via _anonymize
                if _anonymize(video, delete_original_raw=self.delete_source):
                    self.logger.info("VideoFile._anonymize() succeeded")
                    self.processing_context['anonymization_completed'] = True
                    return

            # Strategy 2: Simple copy (no processing, just copy raw to processed)
            self.logger.info("Using simple copy fallback (raw video will be used as 'processed' video)")
            self.processing_context['anonymization_completed'] = False
            self.processing_context['use_raw_as_processed'] = True
            self.logger.warning("Fallback: Video will be imported without anonymization (raw copy used)")
        except Exception as e:
            self.logger.error(f"Error during fallback anonymization: {e}", exc_info=True)
            self.processing_context['anonymization_completed'] = False
            self.processing_context['error_reason'] = str(e)
    def _finalize_processing(self):
        """Finalize processing and update video state."""
        self.logger.info("Updating video processing state...")
        
        with transaction.atomic():
            video = self._require_current_video()
            try:
                video.refresh_from_db()
            except Exception as refresh_error:
                self.logger.warning("Could not refresh VideoFile %s from DB: %s", video.uuid, refresh_error)

            state = video.get_or_create_state()
            
            # Only mark frames as extracted if they were successfully extracted
            if self.processing_context.get('frames_extracted', False):
                state.frames_extracted = True
                self.logger.info("Marked frames as extracted in state")
            else:
                self.logger.warning("Frames were not extracted, not updating state")
                
            # Always mark these as true (metadata extraction attempts were made)
            state.frames_initialized = True
            state.video_meta_extracted = True
            state.text_meta_extracted = True
            
            # ✅ FIX: Only mark as processed if anonymization actually completed
            anonymization_completed = self.processing_context.get('anonymization_completed', False)
            if anonymization_completed:
                state.mark_sensitive_meta_processed(save=False)
                self.logger.info("Anonymization completed - marking sensitive meta as processed")
            else:
                self.logger.warning(
                    "Anonymization NOT completed - NOT marking as processed. "
                    f"Reason: {self.processing_context.get('error_reason', 'Unknown')}"
                )
                # Explicitly mark as NOT processed
                state.sensitive_meta_processed = False
            
            # Save all state changes
            state.save()
            self.logger.info("Video processing state updated")
        
        # Signal completion
        self._signal_completion()

    def _cleanup_and_archive(self):
        """Move processed video to anonym_videos and cleanup."""
        from endoreg_db.utils import data_paths

        anonym_videos_dir = data_paths["anonym_video"]  # /data/anonym_videos
        anonym_videos_dir.mkdir(parents=True, exist_ok=True)

        video = self._require_current_video()

        processed_video_path = None
        if 'cleaned_video_path' in self.processing_context:
            processed_video_path = self.processing_context['cleaned_video_path']
        else:
            raw_video_path = self.processing_context.get('raw_video_path')
            if raw_video_path and Path(raw_video_path).exists():
                video_filename = self.processing_context.get('video_filename', Path(raw_video_path).name)
                processed_filename = f"processed_{video_filename}"
                processed_video_path = Path(raw_video_path).parent / processed_filename
                try:
                    shutil.copy2(str(raw_video_path), str(processed_video_path))
                    self.logger.info("Copied raw video for processing: %s", processed_video_path)
                except Exception as exc:
                    self.logger.error("Failed to copy raw video: %s", exc)
                    processed_video_path = None

        if processed_video_path and Path(processed_video_path).exists():
            try:
                ext = Path(processed_video_path).suffix or ".mp4"
                anonym_video_filename = f"anonym_{video.uuid}{ext}"
                anonym_target_path = anonym_videos_dir / anonym_video_filename

                shutil.move(str(processed_video_path), str(anonym_target_path))
                self.logger.info("Moved processed video to: %s", anonym_target_path)

                if anonym_target_path.exists():
                    try:
                        storage_root = data_paths["storage"]
                        relative_path = anonym_target_path.relative_to(storage_root)
                        video.processed_file.name = str(relative_path)
                        video.save(update_fields=["processed_file"])
                        self.logger.info("Updated processed_file path to: %s", relative_path)
                    except Exception as exc:
                        self.logger.error("Failed to update processed_file path: %s", exc)
                        video.processed_file.name = f"anonym_videos/{anonym_video_filename}"
                        video.save(update_fields=['processed_file'])
                        self.logger.info(
                            "Updated processed_file path using fallback: %s",
                            f"anonym_videos/{anonym_video_filename}",
                        )

                    self.processing_context['anonymization_completed'] = True
                else:
                    self.logger.warning("Processed video file not found after move: %s", anonym_target_path)
            except Exception as exc:
                self.logger.error("Failed to move processed video to anonym_videos: %s", exc)
        else:
            self.logger.warning("No processed video available - processed_file will remain empty")

        try:
            from endoreg_db.utils.paths import RAW_FRAME_DIR
            shutil.rmtree(RAW_FRAME_DIR, ignore_errors=True)
            self.logger.debug("Cleaned up temporary frames directory: %s", RAW_FRAME_DIR)
        except Exception as exc:
            self.logger.warning("Failed to remove directory %s: %s", RAW_FRAME_DIR, exc)

        source_path = self.processing_context['file_path']
        if self.processing_context['delete_source'] and Path(source_path).exists():
            try:
                os.remove(source_path)
                self.logger.info("Removed remaining source file: %s", source_path)
            except Exception as exc:
                self.logger.warning("Failed to remove source file %s: %s", source_path, exc)

        if not video.processed_file or not Path(video.processed_file.path).exists():
            self.logger.warning("No processed_file found after cleanup - video will be unprocessed")
            try:
                video.anonymize(delete_original_raw=self.delete_source)
                video.save(update_fields=['processed_file'])
                self.logger.info("Late-stage anonymization succeeded")
            except Exception as e:
                self.logger.error("Late-stage anonymization failed: %s", e)
                self.processing_context['anonymization_completed'] = False

        self.logger.info("Cleanup and archiving completed")

        self.processed_files.add(str(self.processing_context['file_path']))

        with transaction.atomic():
            video.refresh_from_db()
            if hasattr(video, 'state') and self.processing_context.get('anonymization_completed'):
                video.state.mark_sensitive_meta_processed(save=True)

        self.logger.info("Import and anonymization completed for VideoFile UUID: %s", video.uuid)
        self.logger.info("Raw video stored in: /data/videos")
        self.logger.info("Processed video stored in: /data/anonym_videos")
    
    def _create_sensitive_file(
        self,
        video_instance: VideoFile | None = None,
        file_path: Path | str | None = None,
    ) -> Path:
        """Create or move a sensitive copy of the raw video file inside storage."""

        video = video_instance or self._require_current_video()

        raw_field: FieldFile | None = getattr(video, "raw_file", None)
        source_path: Path | None = None
        try:
            if raw_field and raw_field.path:
                source_path = Path(raw_field.path)
        except Exception:
            source_path = None

        if source_path is None and file_path is not None:
            source_path = Path(file_path)

        if source_path is None:
            raise ValueError("No file path available for creating sensitive file")
        if not raw_field:
            raise ValueError("VideoFile must have a raw_file to create a sensitive file")

        target_dir = VIDEO_DIR / "sensitive"
        if not target_dir.exists():
            self.logger.info("Creating sensitive file directory: %s", target_dir)
            os.makedirs(target_dir, exist_ok=True)

        target_file_path = target_dir / source_path.name
        try:
            shutil.move(str(source_path), str(target_file_path))
            self.logger.info("Moved raw file to sensitive directory: %s", target_file_path)
        except Exception as exc:
            self.logger.warning("Failed to move raw file to sensitive dir, copying instead: %s", exc)
            shutil.copy(str(source_path), str(target_file_path))
            try:
                os.remove(source_path)
            except FileNotFoundError:
                pass

        try:
            from endoreg_db.utils import data_paths

            storage_root = data_paths["storage"]
            relative_path = target_file_path.relative_to(storage_root)
            video.raw_file.name = str(relative_path)
            video.save(update_fields=["raw_file"])
            self.logger.info("Updated video.raw_file to point to sensitive location: %s", relative_path)
        except Exception as exc:
            self.logger.warning("Failed to set relative path, using fallback: %s", exc)
            video.raw_file.name = f"videos/sensitive/{target_file_path.name}"
            video.save(update_fields=["raw_file"])
            self.logger.info(
                "Updated video.raw_file using fallback method: videos/sensitive/%s",
                target_file_path.name,
            )

        self.logger.info("Created sensitive file for %s at %s", video.uuid, target_file_path)
        return target_file_path

    def _get_processor_roi_info(self) -> Tuple[Optional[List[List[Dict[str, Any]]]], Optional[Dict[str, Any]]]:
        """Get processor ROI information for masking."""
        endoscope_data_roi_nested = None
        endoscope_image_roi = None

        video = self._require_current_video()

        try:
            video_meta = getattr(video, "video_meta", None)
            processor = getattr(video_meta, "processor", None) if video_meta else None
            if processor:
                assert isinstance(processor, EndoscopyProcessor), "Processor is not of type EndoscopyProcessor"
                endoscope_image_roi = processor.get_roi_endoscope_image()
                endoscope_data_roi_nested = processor.get_rois()
                self.logger.info("Retrieved processor ROI information: endoscope_image_roi=%s", endoscope_image_roi)
            else:
                self.logger.warning(
                    "No processor found for video %s, proceeding without ROI masking",
                    video.uuid,
                )
        except Exception as exc:
            self.logger.error("Failed to retrieve processor ROI information: %s", exc)

        return endoscope_data_roi_nested, endoscope_image_roi

    def _ensure_default_patient_data(self, video_instance: VideoFile | None = None) -> None:
        """Ensure minimum patient data is present on the video's SensitiveMeta."""

        video = video_instance or self._require_current_video()

        sensitive_meta = getattr(video, "sensitive_meta", None)
        if not sensitive_meta:
            self.logger.info("No SensitiveMeta found for video %s, creating default", video.uuid)
            default_data = {
                "patient_first_name": "Patient",
                "patient_last_name": "Unknown",
                "patient_dob": date(1990, 1, 1),
                "examination_date": date.today(),
                "center_name": video.center.name if video.center else "university_hospital_wuerzburg",
            }
            try:
                sensitive_meta = SensitiveMeta.create_from_dict(default_data)
                video.sensitive_meta = sensitive_meta
                video.save(update_fields=["sensitive_meta"])
                state = video.get_or_create_state()
                state.mark_sensitive_meta_processed(save=True)
                self.logger.info("Created default SensitiveMeta for video %s", video.uuid)
            except Exception as exc:
                self.logger.error("Failed to create default SensitiveMeta for video %s: %s", video.uuid, exc)
                return
        else:
            update_data: Dict[str, Any] = {}
            if not sensitive_meta.patient_first_name:
                update_data["patient_first_name"] = "Patient"
            if not sensitive_meta.patient_last_name:
                update_data["patient_last_name"] = "Unknown"
            if not sensitive_meta.patient_dob:
                update_data["patient_dob"] = date(1990, 1, 1)
            if not sensitive_meta.examination_date:
                update_data["examination_date"] = date.today()

            if update_data:
                try:
                    sensitive_meta.update_from_dict(update_data)
                    state = video.get_or_create_state()
                    state.mark_sensitive_meta_processed(save=True)
                    self.logger.info(
                        "Updated missing SensitiveMeta fields for video %s: %s",
                        video.uuid,
                        list(update_data.keys()),
                    )
                except Exception as exc:
                    self.logger.error("Failed to update SensitiveMeta for video %s: %s", video.uuid, exc)



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

            # make sure lx_anonymizer_path is a Path object
            lx_anonymizer_path = Path(str(lx_anonymizer_path))
            
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

    

    def _perform_frame_cleaning(self, FrameCleaner, endoscope_data_roi_nested, endoscope_image_roi):
        """Perform frame cleaning and anonymization."""
        # Instantiate frame cleaner
        frame_cleaner = FrameCleaner()
        
        # Prepare parameters for frame cleaning
        raw_video_path = self.processing_context.get('raw_video_path')
        
        if not raw_video_path or not Path(raw_video_path).exists():
            raise RuntimeError(f"Raw video path not found: {raw_video_path}")
        
        # Get processor name safely
        video = self._require_current_video()
        video_meta = getattr(video, "video_meta", None)
        processor = getattr(video_meta, "processor", None) if video_meta else None
        device_name = processor.name if processor else self.processing_context['processor_name']
                
        # Create temporary output path for cleaned video
        video_filename = self.processing_context.get('video_filename', Path(raw_video_path).name)
        cleaned_filename = f"cleaned_{video_filename}"
        cleaned_video_path = Path(raw_video_path).parent / cleaned_filename
        
        processor_roi, endoscope_roi = self._get_processor_roi_info(video)
        
        # Processor roi can be used later to OCR preknown regions.
        
        # Clean video with ROI masking (heavy I/O operation)
        actual_cleaned_path, extracted_metadata = frame_cleaner.clean_video(
            video_path=Path(raw_video_path),
            video_file_obj=video,
            endoscope_image_roi=endoscope_image_roi,
            endoscope_data_roi_nested=endoscope_data_roi_nested,
            output_path=cleaned_video_path,
            technique="mask_overlay"
        )
        
        # Optional: enrich metadata using TrOCR+LLM on one random extracted frame
        try:
            # Prefer frames belonging to this video (UUID in path), else pick any frame
            frame_candidates = list(RAW_FRAME_DIR.rglob("*.jpg")) + list(RAW_FRAME_DIR.rglob("*.png"))
            video_uuid = str(video.uuid)
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

    def _update_sensitive_metadata(self, extracted_metadata: Dict[str, Any]):
        """
        Update sensitive metadata with extracted information.
        Args:
            extracted_metadata (Dict[str, Any]): Extracted metadata to update.
        """
        video = self._require_current_video()
        sensitive_meta = getattr(video, "sensitive_meta", None)

        if not (sensitive_meta and extracted_metadata):
            return

        sm = sensitive_meta
        updated_fields = []
        
        try:
            sm.update_from_dict(extracted_metadata)
            updated_fields = list(extracted_metadata.keys())
        except KeyError as e:
            self.logger.warning(f"Failed to update SensitiveMeta field {e}")
            
        if updated_fields:
            sm.save(update_fields=updated_fields)
            self.logger.info("Updated SensitiveMeta fields for video %s: %s", video.uuid, updated_fields)

            state = video.get_or_create_state()
            state.mark_sensitive_meta_processed(save=True)
            self.logger.info("Marked sensitive metadata as processed for video %s", video.uuid)
        else:
            self.logger.info("No SensitiveMeta fields updated for video %s - all existing values preserved", video.uuid)

    def _signal_completion(self):
        """Signal completion to the tracking system."""
        try:
            video = self._require_current_video()

            raw_field: FieldFile | None = getattr(video, "raw_file", None)
            raw_exists = False
            if raw_field and getattr(raw_field, "path", None):
                try:
                    raw_exists = Path(raw_field.path).exists()
                except (ValueError, OSError):
                    raw_exists = False

            video_processing_complete = (
                video.sensitive_meta is not None and
                video.video_meta is not None and
                raw_exists
            )

            if video_processing_complete:
                self.logger.info("Video %s processing completed successfully - ready for validation", video.uuid)

                # Update completion flags if they exist
                completion_fields = []
                for field_name in ['import_completed', 'processing_complete', 'ready_for_validation']:
                    if hasattr(video, field_name):
                        setattr(video, field_name, True)
                        completion_fields.append(field_name)
                
                if completion_fields:
                    video.save(update_fields=completion_fields)
                    self.logger.info("Updated completion flags: %s", completion_fields)
            else:
                self.logger.warning(
                    "Video %s processing incomplete - missing required components",
                    video.uuid,
                )
                
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
        """
        Cleanup processing context and release file lock.
        
        This method is always called in the finally block of import_and_anonymize()
        to ensure the file lock is released even if processing fails.
        """
        try:
            # Release file lock if it was acquired
            lock_context = self.processing_context.get('_lock_context')
            if lock_context is not None:
                try:
                    lock_context.__exit__(None, None, None)
                    self.logger.info("Released file lock")
                except Exception as e:
                    self.logger.warning(f"Error releasing file lock: {e}")
            
            # Remove file from processed set if processing failed
            file_path = self.processing_context.get('file_path')
            if file_path and not self.processing_context.get('anonymization_completed'):
                file_path_str = str(file_path)
                if file_path_str in self.processed_files:
                    self.processed_files.remove(file_path_str)
                    self.logger.info(f"Removed {file_path_str} from processed files (failed processing)")
            
                 
            
            
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
) -> VideoFile | None:
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
"""
Video import service module.

Provides high-level functions for importing and anonymizing video files,
combining VideoFile creation with frame-level anonymization.

Changelog:
    October 14, 2025: Added file locking mechanism to prevent race conditions
                      during concurrent video imports (matches PDF import pattern)
"""

import logging
import os
import shutil
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import subprocess
from django.db import transaction
from django.db.models.fields.files import FieldFile

from endoreg_db.models import EndoscopyProcessor, SensitiveMeta, VideoFile
from endoreg_db.models.media.video.video_file_anonymize import _cleanup_raw_assets
from endoreg_db.utils import ensure_local_file, storage_file_exists
from endoreg_db.utils.hashs import get_video_hash
from endoreg_db.utils.paths import ANONYM_VIDEO_DIR, STORAGE_DIR, VIDEO_DIR
from endoreg_db.models.state import VideoState

# File lock configuration (matches PDF import)
STALE_LOCK_SECONDS = 6000  # 100 minutes - reclaim locks older than this
MAX_LOCK_WAIT_SECONDS = 90  # New: wait up to 90s for a non-stale lock to clear before skipping

logger = logging.getLogger(__name__)


class VideoImportService:
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
        """
        Initialize the VideoImportService instance and its processing state.
        
        Sets the project root, prepares the anonymized-video tracking set (creating the anonym_videos directory if missing), and initializes core instance attributes used during import and anonymization.
        
        Parameters:
            project_root (Optional[Path]): Optional path to the project root. If omitted, the project root is inferred relative to this file.
        """
        if project_root:
            self.project_root = Path(project_root)
        else:
            self.project_root = Path(__file__).parent.parent.parent.parent

        # Track processed files to prevent duplicates
        try:
            # Ensure anonym_video directory exists before listing files
            anonym_video_dir = Path(ANONYM_VIDEO_DIR)
            if anonym_video_dir.exists():
                self.processed_files = set(str(anonym_video_dir / file) for file in os.listdir(ANONYM_VIDEO_DIR))
            else:
                logger.info(f"Creating anonym_videos directory: {anonym_video_dir}")
                anonym_video_dir.mkdir(parents=True, exist_ok=True)
                self.processed_files = set()
        except Exception as e:
            logger.warning(f"Failed to initialize processed files tracking: {e}")
            self.processed_files = set()

        # Central video instance and processing context
        self.current_video: Optional[VideoFile] = None
        self.processing_context: Dict[str, Any] = {}

        self.delete_source = True
        self.original_file_path = None

        self.logger = logging.getLogger(__name__)
        
        self.current_video_id = Optional[int]

        self.cleaner = None  # This gets instantiated in the perform_frame_cleaning method

    def _require_current_video(self) -> VideoFile:
        """
        Get the current VideoFile instance.
        
        Returns:
            VideoFile: The current VideoFile.
        
        Raises:
            RuntimeError: If no current video has been initialized.
        """
        if self.current_video is None:
            raise RuntimeError("Current video instance is not set")
        return self.current_video

    @contextmanager
    def _file_lock(self, path: Path):
        """
        Acquire a filesystem lock for the given file by creating a sibling ".lock" file.
        
        Context manager that creates a ".lock" file next to the provided path to serialize processing of the same file. If an existing lock is older than STALE_LOCK_SECONDS it will be reclaimed; otherwise the manager waits up to MAX_LOCK_WAIT_SECONDS for the lock to clear. The lock file is removed when the context exits.
        
        Parameters:
            path (Path): Path to the target file to lock; the lock file will be created at `path.with_suffix(path.suffix + ".lock")` (i.e., by appending ".lock" to the file path).
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
                                path,
                                age,
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
        return getattr(self, "_processed", False)

    def import_and_anonymize(
        self,
        file_path: Union[Path, str],
        center_name: str,
        processor_name: str,
        save_video: bool = True,
        delete_source: bool = True,
    ) -> "VideoFile|None":
        """
        Orchestrates end-to-end import and anonymization of a single video file into the system.
        
        Performs file validation and locking, creates or retrieves the VideoFile record, creates a sensitive copy,
        prepares the processing environment, runs frame-level anonymization (or fallback), finalizes state changes,
        and moves processed output into permanent storage.
        
        Parameters:
            file_path (Union[Path, str]): Path to the source video file to import.
            center_name (str): Name of the center to associate with the imported video.
            processor_name (str): Name of the processor configuration to use for anonymization.
            save_video (bool): If True, preserve the processed (anonymized) video in storage; if False, do not store processed output.
            delete_source (bool): If True, remove the original source file after successful import and archival.
        
        Returns:
            VideoFile|None: The VideoFile instance for the imported video when processing was performed; `None` if processing was skipped
            because another worker was handling the same file.
        """
        # DEFENSIVE: Initialize processing_context immediately to prevent KeyError crashes
        self.processing_context = {"file_path": Path(file_path)}

        try:
            # Initialize processing context
            self._initialize_processing_context(file_path, center_name, processor_name, save_video, delete_source)

            # Validate and prepare file (may raise ValueError if another worker holds a non-stale lock)
            try:
                self._validate_and_prepare_file()
            except ValueError as ve:
                # Relaxed behavior: if another process is working on this file, skip cleanly
                if "already being processed" in str(ve):
                    self.logger.info(f"Skipping {file_path}: {ve}")
                    return None
                raise

            # Create or retrieve video instance
            self._create_or_retrieve_video_instance()

            # Create sensitive meta file, ensure raw is moved out of processing folder watched by file watcher.
            self._create_sensitive_file()

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
            # Safe file path access - handles cases where processing_context wasn't initialized
            safe_file_path = getattr(self, "processing_context", {}).get("file_path", file_path)
            # Debug: Log context state for troubleshooting
            context_keys = list(getattr(self, "processing_context", {}).keys())
            self.logger.debug(f"Context keys during error: {context_keys}")
            self.logger.error(f"Video import and anonymization failed for {safe_file_path}: {e}")
            self._cleanup_on_error()
            raise
        finally:
            self._cleanup_processing_context()

    def _initialize_processing_context(
        self,
        file_path: Union[Path, str],
        center_name: str,
        processor_name: str,
        save_video: bool,
        delete_source: bool,
    ):
        """Initialize the processing context for the current video import."""
        self.processing_context = {
            "file_path": Path(file_path),
            "center_name": center_name,
            "processor_name": processor_name,
            "save_video": save_video,
            "delete_source": delete_source,
            "processing_started": False,
            "frames_extracted": False,
            "anonymization_completed": False,
            "error_reason": None,
        }
        self.original_file_path = str(file_path)

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
        file_path = self.processing_context["file_path"]

        # Acquire file lock to prevent concurrent processing
        # Lock will be held until finally block in import_and_anonymize()
        try:
            self.processing_context["_lock_context"] = self._file_lock(file_path)
            self.processing_context["_lock_context"].__enter__()
        except Exception:
            self._cleanup_processing_context()
            raise

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

        self.logger.info("Creating VideoFile instance...")

        self.current_video = VideoFile.create_from_file_initialized(
            file_path=self.processing_context["file_path"],
            center_name=self.processing_context["center_name"],
            processor_name=self.processing_context["processor_name"],
            delete_source=self.processing_context["delete_source"],
            save_video_file=self.processing_context["save_video"],
        )
        self.current_video_id = self.current_video.pk

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
        self.processing_context["processing_started"] = True

    def _move_to_final_storage(self):
        """
        Move video from raw_videos to final storage locations.
        - Raw video → /data/videos (raw_file_path)
        - Processed video will later → /data/anonym_videos (file_path)
        """
        from endoreg_db.utils import data_paths

        source_path = Path(self.processing_context["file_path"])
        _current_video = self._require_current_video()
        videos_dir = Path(data_paths["video"])
        storage_root = Path(data_paths["storage"])

        videos_dir.mkdir(parents=True, exist_ok=True)

        # --- Derive stored_raw_path safely ---
        stored_raw_path = None
        try:
            if hasattr(_current_video, "get_raw_file_path"):
                candidate = _current_video.get_raw_file_path()
                if candidate:
                    candidate_path = Path(candidate)
                    # Accept only if under storage_root
                    try:
                        candidate_path.relative_to(storage_root)
                        stored_raw_path = candidate_path
                    except ValueError:
                        # outside storage_root, reset
                        stored_raw_path = None
        except Exception:
            stored_raw_path = None

        # Fallback: derive from UUID + suffix - ALWAYS use UUID for consistency
        if not stored_raw_path:
            suffix = source_path.suffix or ".mp4"
            uuid_str = getattr(_current_video, "uuid", None)
            if uuid_str:
                filename = f"{uuid_str}{suffix}"
            else:
                # Emergency fallback with timestamp to avoid conflicts
                import time

                timestamp = int(time.time())
                filename = f"video_{timestamp}{suffix}"
                self.logger.warning("No UUID available, using timestamp-based filename: %s", filename)
            stored_raw_path = videos_dir / filename
            self.logger.debug("Using UUID-based raw filename: %s", filename)

        delete_source = bool(self.processing_context.get("delete_source", True))
        stored_raw_path.parent.mkdir(parents=True, exist_ok=True)

        # --- Move or copy raw video ---
        try:
            if delete_source:
                # Try atomic move first, fallback to copy+unlink
                try:
                    os.replace(source_path, stored_raw_path)
                    self.logger.info("Moved raw video to: %s", stored_raw_path)
                except Exception:
                    shutil.copy2(source_path, stored_raw_path)
                    os.remove(source_path)
                    self.logger.info("Copied & removed raw video to: %s", stored_raw_path)
            else:
                shutil.copy2(source_path, stored_raw_path)
                self.logger.info("Copied raw video to: %s", stored_raw_path)
        except Exception as e:
            self.logger.error("Failed to move/copy video to final storage: %s", e)
            raise

        # --- Ensure DB raw_file is relative to storage root ---
        try:
            rel_path = stored_raw_path.relative_to(storage_root)
        except Exception:
            rel_path = Path("videos") / stored_raw_path.name

        if _current_video.raw_file.name != rel_path.as_posix():
            _current_video.raw_file.name = rel_path.as_posix()
            _current_video.save(update_fields=["raw_file"])
            self.logger.info("Updated raw_file path to: %s", rel_path.as_posix())

        # --- Store for later stages ---
        self.processing_context["raw_video_path"] = stored_raw_path
        self.processing_context["video_filename"] = stored_raw_path.name

    def _setup_processing_environment(self):
        """
        Prepare the video processing environment for anonymization without moving files.
        
        Initializes video specifications, attempts to pre-extract frames and create frame records, and persists the frames_extracted flag both in the processing context and on the VideoState when extraction succeeds. If frame extraction fails, records failure in the processing context and continues. Ensures default patient/sensitive metadata exists for the video.
        """
        video = self._require_current_video()

        # Initialize video specifications
        video.initialize_video_specs()

        # Extract frames BEFORE processing to prevent pipeline 1 conflicts
        self.logger.info("Pre-extracting frames to avoid pipeline conflicts...")
        try:
            frames_extracted = video.extract_frames(overwrite=False)
            if frames_extracted:
                self.processing_context["frames_extracted"] = True
                self.logger.info("Frame extraction completed successfully")
                # Initialize frame objects in database
                video.initialize_frames(video.get_frame_paths())

                # CRITICAL: Immediately save the frames_extracted state to database
                # to prevent refresh_from_db() in pipeline 1 from overriding it
                state = video.get_or_create_state()
                if not state.frames_extracted:
                    state.frames_extracted = True
                    state.save(update_fields=["frames_extracted"])
                    self.logger.info("Persisted frames_extracted=True to database")
            else:
                self.logger.warning("Frame extraction failed, but continuing...")
                self.processing_context["frames_extracted"] = False
        except Exception as e:
            self.logger.warning(f"Frame extraction failed during setup: {e}, but continuing...")
            self.processing_context["frames_extracted"] = False

        # Ensure default patient data
        self._ensure_default_patient_data(video_instance=video)

        self.logger.info("Processing environment setup completed")

    def _process_frames_and_metadata(self):
        """
        Process frames and extract anonymization metadata for the current video.
        
        Attempts frame-level anonymization using the configured frame cleaner and processor ROI masking when a raw file is present and frame cleaning is available. Runs the cleaning operation with a long timeout (5000 seconds) and, on timeout, waits a grace period (60 seconds) for a cleaned output file to appear before treating the attempt as failed. On success, records cleaned path and marks anonymization as completed in the processing context. If frame cleaning is unavailable, times out, or raises an error, falls back to the simpler anonymization path and records failure details in the processing context when both primary and fallback approaches fail.
        """
        # Check frame cleaning availability
        frame_cleaning_available, frame_cleaner = self._ensure_frame_cleaning_available()
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
            from concurrent.futures import ThreadPoolExecutor
            from concurrent.futures import TimeoutError as FutureTimeoutError

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    self._perform_frame_cleaning,
                    endoscope_data_roi_nested,
                    endoscope_image_roi,
                )
                try:
                    # Increased timeout to better accommodate ffmpeg + OCR
                    future.result(timeout=5000)
                    self.processing_context["anonymization_completed"] = True
                    self.logger.info("Frame cleaning completed successfully within timeout")
                except FutureTimeoutError:
                    self.logger.warning("Frame cleaning timed out; entering grace period check for cleaned output")
                    # Grace period: detect if cleaned file appears shortly after timeout
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
                                self.processing_context["cleaned_video_path"] = expected_cleaned_path
                                self.processing_context["anonymization_completed"] = True
                                self.logger.info(
                                    "Detected cleaned video during grace period: %s",
                                    expected_cleaned_path,
                                )
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
                self.processing_context["anonymization_completed"] = False
                self.processing_context["error_reason"] = f"Frame cleaning failed: {e}, Fallback failed: {fallback_error}"

    def _save_anonymized_video(self):
        """
        Finalize and persist the anonymized video for the current VideoFile, update related database fields, and optionally schedule raw-asset cleanup.
        
        This method verifies the assembled anonymized file exists, computes and checks its hash against other videos to prevent duplicates, sets the video's processed hash and processed file location, and updates the video's frame directory. If delete_source is enabled, it clears the video's raw_file, schedules deletion of the original raw file and frame directory after the transaction commits, and updates raw-related fields. It ensures a VideoState exists and marks the video as anonymized.
        
        Returns:
            True if the anonymized video was saved and the video state was marked anonymized.
        
        Raises:
            RuntimeError: if the assembled anonymized file is missing or the video state cannot be retrieved/created.
            ValueError: if another video already has the same processed video hash.
        """
        original_raw_file_path_to_delete = None
        original_raw_frame_dir_to_delete = None
        video = self._require_current_video()
        anonymized_video_path = video.get_target_anonymized_video_path()

        if not anonymized_video_path.exists():
            raise RuntimeError(f"Processed video file not found after assembly for {video.uuid}: {anonymized_video_path}")

        new_processed_hash = get_video_hash(anonymized_video_path)
        if video.__class__.objects.filter(processed_video_hash=new_processed_hash).exclude(pk=video.pk).exists():
            raise ValueError(f"Processed video hash {new_processed_hash} already exists for another video (Video: {video.uuid}).")

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

            video.raw_file.name = ""

            update_fields.extend(["raw_file", "video_hash"])

            transaction.on_commit(
                lambda: _cleanup_raw_assets(
                    video_uuid=video.uuid,
                    raw_file_path=original_raw_file_path_to_delete,
                    raw_frame_dir=original_raw_frame_dir_to_delete,
                )
            )

        video.save(update_fields=update_fields)
        if not isinstance(video.state, VideoState):
            try:
                video.get_or_create_state()
            except ValueError as e:
                raise RuntimeError(f"Video state not found for video {video.uuid}. Error {e}")

        else:
            video.state.mark_anonymized(save=True)
            video.refresh_from_db()
            self.current_video = video

            return True

    def _fallback_anonymize_video(self):
        """
        Mark the current video as processed using a non-anonymized fallback and update processing context flags.
        
        Sets processing_context["anonymization_completed"] to False and processing_context["use_raw_as_processed"] to True to indicate the raw file will be treated as the processed (anonymized) output. If an error occurs, records the exception message in processing_context["error_reason"] and ensures anonymization is marked incomplete.
        """
        try:
            self.logger.info("Attempting fallback video anonymization...")
            video = self.current_video
            if video is None:
                self.logger.warning("No VideoFile instance available for fallback anonymization")

            # Strategy 2: Simple copy (no processing, just copy raw to processed)
            self.logger.info("Using simple copy fallback (raw video will be used as 'processed' video)")
            self.processing_context["anonymization_completed"] = False
            self.processing_context["use_raw_as_processed"] = True
            self.logger.warning("Fallback: Video will be imported without anonymization (raw copy used)")
        except Exception as e:
            self.logger.error(f"Error during fallback anonymization: {e}", exc_info=True)
            self.processing_context["anonymization_completed"] = False
            self.processing_context["error_reason"] = str(e)

    def _finalize_processing(self):
        """
        Finalize the video's processing lifecycle and persist resulting state flags.
        
        Refreshes the current VideoFile from the database if possible, then within a transaction updates its VideoState according to the current processing context: sets frames_extracted when frames were actually extracted; sets frames_initialized, video_meta_extracted, and text_meta_extracted to true to record extraction attempts; marks sensitive metadata as processed only when anonymization_completed is true (otherwise ensures sensitive_meta_processed is false). Persists the state changes and then signals overall completion via the service's completion check. Logs refresh and decision outcomes for observability.
        """
        self.logger.info("Updating video processing state...")

        with transaction.atomic():
            video = self._require_current_video()
            try:
                video.refresh_from_db()
            except Exception as refresh_error:
                self.logger.warning(
                    "Could not refresh VideoFile %s from DB: %s",
                    video.uuid,
                    refresh_error,
                )

            state = video.get_or_create_state()

            # Only mark frames as extracted if they were successfully extracted
            if self.processing_context.get("frames_extracted", False):
                state.frames_extracted = True
                self.logger.info("Marked frames as extracted in state")
            else:
                self.logger.warning("Frames were not extracted, not updating state")

            # Always mark these as true (metadata extraction attempts were made)
            state.frames_initialized = True
            state.video_meta_extracted = True
            state.text_meta_extracted = True

            # ✅ FIX: Only mark as processed if anonymization actually completed
            anonymization_completed = self.processing_context.get("anonymization_completed", False)
            if anonymization_completed:
                state.mark_sensitive_meta_processed(save=False)
                self.logger.info("Anonymization completed - marking sensitive meta as processed")
            else:
                self.logger.warning(f"Anonymization NOT completed - NOT marking as processed. Reason: {self.processing_context.get('error_reason', 'Unknown')}")
                # Explicitly mark as NOT processed
                state.sensitive_meta_processed = False

            # Save all state changes
            state.save()
            self.logger.info("Video processing state updated")

        # Signal completion
        self._signal_completion()

    def _cleanup_and_archive(self):
        """
        Move the processed video into the anonym_videos storage and perform cleanup and finalization.
        
        This method moves or copies the available processed video into the anonym_videos directory, updates the VideoFile.processed_file path in storage, and sets a flag in the processing context when anonymization is completed. It also removes temporary frame directories, optionally deletes the original source file if configured, and — if no processed file was produced — attempts a late-stage anonymization and updates the processing flag accordingly. Finally, it records the original file as processed in memory and, within a database transaction, ensures the video's state exists and marks sensitive metadata as processed.
        """
        from endoreg_db.utils import data_paths

        anonym_videos_dir = data_paths["anonym_video"]  # /data/anonym_videos
        anonym_videos_dir.mkdir(parents=True, exist_ok=True)

        video = self._require_current_video()

        processed_video_path = None
        if "cleaned_video_path" in self.processing_context:
            processed_video_path = self.processing_context["cleaned_video_path"]
        else:
            raw_video_path = self.processing_context.get("raw_video_path")
            if raw_video_path and Path(raw_video_path).exists():
                # Use UUID-based naming to avoid conflicts
                suffix = Path(raw_video_path).suffix or ".mp4"
                processed_filename = f"processed_{video.uuid}{suffix}"
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
                        video.save(update_fields=["processed_file"])
                        self.logger.info(
                            "Updated processed_file path using fallback: %s",
                            f"anonym_videos/{anonym_video_filename}",
                        )

                    self.processing_context["anonymization_completed"] = True
                else:
                    self.logger.warning(
                        "Processed video file not found after move: %s",
                        anonym_target_path,
                    )
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

        source_path = self.processing_context["file_path"]
        if self.processing_context["delete_source"] and Path(source_path).exists():
            try:
                os.remove(source_path)
                self.logger.info("Removed remaining source file: %s", source_path)
            except Exception as exc:
                self.logger.warning("Failed to remove source file %s: %s", source_path, exc)

        if not video.processed_file or not storage_file_exists(video.processed_file):
            self.logger.warning("No processed_file found after cleanup - video will be unprocessed")
            try:
                video.anonymize(delete_original_raw=self.delete_source)
                video.save(update_fields=["processed_file"])
                self.logger.info("Late-stage anonymization succeeded")
            except Exception as e:
                self.logger.error("Late-stage anonymization failed: %s", e)
                self.processing_context["anonymization_completed"] = False

        self.logger.info("Cleanup and archiving completed")

        self.processed_files.add(str(self.processing_context["file_path"]))

        with transaction.atomic():
            video.refresh_from_db()
            if hasattr(video, "state") and self.processing_context.get("anonymization_completed"):
                if not isinstance(video.state, VideoState):
                    try:
                        video.get_or_create_state()
                    except:
                        raise RuntimeError(f"Video state not found for video {video.uuid}")

                video.state.mark_sensitive_meta_processed(save=True)

        self.logger.info("Import and anonymization completed for VideoFile UUID: %s", video.uuid)
        self.logger.info("Raw video stored in: /data/videos")
        self.logger.info("Processed video stored in: /data/anonym_videos")

    def _create_sensitive_file(
        self,
        video_instance: VideoFile | None = None,
        file_path: Path | str | None = None,
    ) -> Path:
        """
        Ensure a sensitive copy of the video's raw file exists in storage and update the VideoFile to reference it.
        
        Creates or copies the source raw video into the storage "videos/sensitive" directory, updates the VideoFile.raw_file to point at the sensitive copy (saving the change), and records the resulting path and filename in the processing context.
        
        Parameters:
            video_instance (VideoFile | None): Optional VideoFile to operate on. If omitted, the current video is required.
            file_path (Path | str | None): Optional filesystem path to use as a source if the VideoFile's raw file is not available.
        
        Returns:
            Path: Filesystem path to the created sensitive copy within storage.
        
        Raises:
            ValueError: If no source file could be located to create the sensitive copy, or if the VideoFile has no raw_file to update.
        
        Side effects:
            - Copies or moves a file into the storage sensitive directory.
            - Updates and saves video.raw_file to reference the sensitive file.
            - Sets processing_context["raw_video_path"] and processing_context["video_filename"].
        """

        video = video_instance or self._require_current_video()
        raw_field: FieldFile | None = getattr(video, "raw_file", None)

        def copy_into_sensitive(source: Path) -> Path:
            """
            Copy a file into the service's sensitive video directory and return the destination path.
            
            Parameters:
                source (Path): Path to the source file to copy.
            
            Returns:
                target_file_path (Path): Path inside VIDEO_DIR/"sensitive" where the file was copied (may be the same as source if already located there).
            """
            target_dir = VIDEO_DIR / "sensitive"
            if not target_dir.exists():
                self.logger.info("Creating sensitive file directory: %s", target_dir)
                os.makedirs(target_dir, exist_ok=True)

            target_name = source.name or "raw_video"
            target_file_path = target_dir / target_name

            if source != target_file_path:
                try:
                    shutil.copy2(source, target_file_path)
                    self.logger.info("Copied raw file to sensitive directory: %s", target_file_path)
                except Exception as exc:
                    self.logger.warning("Failed to copy raw file to sensitive dir: %s", exc)
                    shutil.copy(source, target_file_path)
                    self.logger.info(
                        "Fallback copy succeeded for sensitive directory: %s",
                        target_file_path,
                    )
            else:
                self.logger.debug(
                    "Source path already in sensitive directory: %s",
                    target_file_path,
                )

            return target_file_path

        target_file_path: Path | None = None

        # Prefer an on-disk path from the FieldFile when available
        if raw_field:
            try:
                local_candidate = Path(raw_field.path)
                if local_candidate.exists():
                    target_file_path = copy_into_sensitive(local_candidate)
            except Exception:
                target_file_path = None

            if target_file_path is None and storage_file_exists(raw_field):
                try:
                    with ensure_local_file(raw_field) as temp_source:
                        target_file_path = copy_into_sensitive(Path(temp_source))
                except Exception as exc:
                    self.logger.warning("Failed to download raw_field for sensitive copy: %s", exc)

        if target_file_path is None and file_path is not None:
            file_candidate = Path(file_path)
            if file_candidate.exists():
                target_file_path = copy_into_sensitive(file_candidate)

        if target_file_path is None:
            context_path = self.processing_context.get("raw_video_path")
            if context_path:
                context_candidate = Path(context_path)
                if context_candidate.exists():
                    target_file_path = copy_into_sensitive(context_candidate)

        if target_file_path is None:
            raise ValueError("No file path available for creating sensitive file")
        if not raw_field:
            raise ValueError("VideoFile must have a raw_file to create a sensitive file")

        try:
            from endoreg_db.utils import data_paths

            storage_root = data_paths["storage"]
            relative_path = target_file_path.relative_to(storage_root)
            video.raw_file.name = relative_path.as_posix()
            video.save(update_fields=["raw_file"])
            self.logger.info(
                "Updated video.raw_file to point to sensitive location: %s",
                relative_path,
            )
        except Exception as exc:
            self.logger.warning("Failed to set relative path, using fallback: %s", exc)
            video.raw_file.name = f"videos/sensitive/{target_file_path.name}"
            video.save(update_fields=["raw_file"])
            self.logger.info(
                "Updated video.raw_file using fallback method: videos/sensitive/%s",
                target_file_path.name,
            )

        self.processing_context["raw_video_path"] = target_file_path
        self.processing_context["video_filename"] = target_file_path.name

        self.logger.info("Created sensitive file for %s at %s", video.uuid, target_file_path)
        return target_file_path

    def _get_processor_roi_info(
        self,
    ) -> Tuple[Optional[List[List[Dict[str, Any]]]], Optional[Dict[str, Any]]]:
        """
        Retrieve ROI information from the current video's processor for use in masking.
        
        Attempts to read sensitive ROI groups and an endoscope-image ROI from the video's processor. If the processor provides a dict of ROIs, it is converted to the nested list shape returned by this function. On failure or when no processor is present, returns (None, None).
        
        Returns:
            Tuple[Optional[List[List[Dict[str, Any]]]], Optional[Dict[str, Any]]]:
                A pair where the first element is a nested list of sensitive ROI dictionaries (groups of ROI dicts) or `None`,
                and the second element is a dictionary of endoscope-image ROI parameters or `None`.
        """
        endoscope_data_roi_nested = None
        endoscope_image_roi = None

        video = self._require_current_video()

        try:
            video_meta = getattr(video, "video_meta", None)
            processor = getattr(video_meta, "processor", None) if video_meta else None
            if processor:
                assert isinstance(processor, EndoscopyProcessor), "Processor is not of type EndoscopyProcessor"
                endoscope_image_roi = processor.get_roi_endoscope_image()
                endoscope_data_roi_nested = processor.get_sensitive_rois()
                self.logger.info(
                    "Retrieved processor ROI information: endoscope_image_roi=%s",
                    endoscope_image_roi,
                )
            else:
                self.logger.warning(
                    "No processor found for video %s, proceeding without ROI masking",
                    video.uuid,
                )
        except Exception as exc:
            self.logger.error("Failed to retrieve processor ROI information: %s", exc)

        # Convert dict to nested list if necessary to match return type
        if isinstance(endoscope_data_roi_nested, dict):
            # Convert dict[str, dict[str, int | None] | None] to List[List[Dict[str, Any]]]
            converted_roi = []
            for key, value in endoscope_data_roi_nested.items():
                if isinstance(value, dict):
                    converted_roi.append([value])
                elif value is None:
                    converted_roi.append([])
            endoscope_data_roi_nested = converted_roi

        return endoscope_data_roi_nested, endoscope_image_roi

    def _ensure_default_patient_data(self, video_instance: VideoFile | None = None) -> None:
        """
        Ensure a VideoFile has minimal sensitive patient metadata and mark it processed.
        
        If the video has no SensitiveMeta, creates and attaches a default SensitiveMeta with placeholder
        patient and examination values and saves the video. If the video already has SensitiveMeta,
        marks that sensitive metadata as processed on the video's state. If creation fails, the video
        is left unchanged.
        Parameters:
            video_instance (VideoFile | None): Optional VideoFile to operate on. If omitted, the
                current service video is used.
        """

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
                self.logger.info("Created default SensitiveMeta for video %s", video.uuid)
            except Exception as exc:
                self.logger.error(
                    "Failed to create default SensitiveMeta for video %s: %s",
                    video.uuid,
                    exc,
                )
                return
        else:
            state = video.get_or_create_state()
            state.mark_sensitive_meta_processed(save=True)


    def _ensure_frame_cleaning_available(self):
        """
        Check for availability of lx_anonymizer's FrameCleaner and return an instance when present.
        
        Attempts to import and instantiate FrameCleaner from the lx_anonymizer package.
        
        Returns:
            tuple: (available, frame_cleaner)
                available (bool): True if FrameCleaner was successfully imported and instantiated, False otherwise.
                frame_cleaner (object|None): An instance of FrameCleaner when available, otherwise None.
        """
        try:
            from lx_anonymizer import FrameCleaner
        except Exception as e:
            self.logger.warning(f"Frame cleaning not available: {e} Please install or update lx_anonymizer.")
            _available = False
            FrameCleaner = None

        assert FrameCleaner is not None
        frame_cleaner = FrameCleaner()
        _available = True

        return _available, frame_cleaner

    def _perform_frame_cleaning(self, endoscope_data_roi_nested, endoscope_image_roi):
        """
        Perform frame-level anonymization of the current video using the configured FrameCleaner.
        
        Performs ROI-based cleaning and stores results in the service processing context. On success,
        the cleaned video path is saved to processing_context["cleaned_video_path"], extracted metadata
        is saved to processing_context["extracted_metadata"], and SensitiveMeta on the current video
        is updated from the extracted metadata.
        
        Parameters:
            endoscope_data_roi_nested (list[list[tuple]]): Nested ROI coordinate data per endoscope channel
                used to drive masking (structure produced by processor ROI extraction).
            endoscope_image_roi (dict | None): Optional image-space ROI hints (e.g., bounding boxes or masks)
                to guide the FrameCleaner.
        
        Raises:
            RuntimeError: If frame cleaning is unavailable, FrameCleaner cannot be instantiated, or the raw
                video path cannot be resolved.
        """
        # Instantiate frame cleaner
        is_available, frame_cleaner = self._ensure_frame_cleaning_available()

        if not is_available or frame_cleaner is None:
            raise RuntimeError("Frame cleaning not available")

        # Prepare parameters for frame cleaning
        raw_video_path = self.processing_context.get("raw_video_path")

        if not raw_video_path or not Path(raw_video_path).exists():
            try:
                self.current_video = self._require_current_video()
                raw_video_path = self.current_video.get_raw_file_path()
            except Exception:
                raise RuntimeError(f"Raw video path not found: {raw_video_path}")

        # Create temporary output path for cleaned video using UUID to avoid naming conflicts
        video = self._require_current_video()
        # Ensure raw_video_path is not None
        if not raw_video_path:
            raise RuntimeError("raw_video_path is None, cannot construct cleaned_video_path")
        suffix = Path(raw_video_path).suffix or ".mp4"
        cleaned_filename = f"cleaned_{video.uuid}{suffix}"
        cleaned_video_path = Path(raw_video_path).parent / cleaned_filename
        self.logger.debug("Using UUID-based cleaned filename: %s", cleaned_filename)

        # Clean video with ROI masking (heavy I/O operation)
        actual_cleaned_path, extracted_metadata = frame_cleaner.clean_video(
            video_path=Path(raw_video_path),
            endoscope_image_roi=endoscope_image_roi,
            endoscope_data_roi_nested=endoscope_data_roi_nested,
            output_path=cleaned_video_path,
            technique="mask_overlay",
        )

        # Store cleaned video path for later use in _cleanup_and_archive
        self.processing_context["cleaned_video_path"] = actual_cleaned_path
        self.processing_context["extracted_metadata"] = extracted_metadata

        # Update sensitive metadata with extracted information
        self._update_sensitive_metadata(extracted_metadata)
        self.logger.info(f"Extracted metadata from frame cleaning: {extracted_metadata}")

        self.logger.info(f"Frame cleaning with ROI masking completed: {actual_cleaned_path}")
        self.logger.info("Cleaned video will be moved to anonym_videos during cleanup")

    def _update_sensitive_metadata(self, extracted_metadata: Dict[str, Any]):
        """
        Populate the current video's SensitiveMeta with values extracted from frame/anonymization metadata.
        
        Updates the existing SensitiveMeta on the current VideoFile (if present) using keys from the provided extracted_metadata. If the SensitiveMeta lacks a center, the method will attempt to set a center object from the VideoFile or resolve a center by the provided "center_name". After applying updates, the SensitiveMeta is saved and the video's processing state is marked as having sensitive metadata processed.
        
        Parameters:
            extracted_metadata (Dict[str, Any]): Metadata extracted during processing to apply to the SensitiveMeta. Keys correspond to SensitiveMeta fields; a "center_name" key may be used to resolve and set a Center object when the SensitiveMeta has no center.
        
        Raises:
            Exception: Re-raises unexpected exceptions raised while saving SensitiveMeta so callers can handle fallback/error flows.
        """
        video = self._require_current_video()
        sensitive_meta = getattr(video, "sensitive_meta", None)

        if not (sensitive_meta and extracted_metadata):
            return

        sm = sensitive_meta
        updated_fields = []

        # Ensure center is set from video.center if not in extracted_metadata
        metadata_to_update = extracted_metadata.copy()

        # FIX: Set center object instead of center_name string
        if not hasattr(sm, "center") or not sm.center:
            if video.center:
                metadata_to_update["center"] = video.center
                self.logger.debug(
                    "Added center object '%s' to metadata for SensitiveMeta update",
                    video.center.name,
                )
            else:
                center_name = metadata_to_update.get("center_name")
                if center_name:
                    try:
                        from ..models.administration import Center

                        center_obj = Center.objects.get(name=center_name)
                        metadata_to_update["center"] = center_obj
                        self.logger.debug("Loaded center object '%s' from center_name", center_name)
                        metadata_to_update.pop("center_name", None)
                    except Center.DoesNotExist:
                        self.logger.error("Center '%s' not found in database", center_name)
                        return

        try:
            sm.update_from_dict(metadata_to_update)
            updated_fields = list(extracted_metadata.keys())  # Only log originally extracted fields
        except KeyError as e:
            self.logger.warning(f"Failed to update SensitiveMeta field {e}")
            return

        if updated_fields:
            try:
                sm.save()  # Remove update_fields to allow all necessary fields to be saved
                self.logger.info(
                    "Updated SensitiveMeta fields for video %s: %s",
                    video.uuid,
                    updated_fields,
                )

                state = video.get_or_create_state()
                state.mark_sensitive_meta_processed(save=True)
                self.logger.info("Marked sensitive metadata as processed for video %s", video.uuid)
            except Exception as e:
                self.logger.error(f"Failed to save SensitiveMeta: {e}")
                raise  # Re-raise to trigger fallback in calling method
        else:
            self.logger.info(
                "No SensitiveMeta fields updated for video %s - all existing values preserved",
                video.uuid,
            )

    def _signal_completion(self):
        """
        Mark the current video's completion flags and log completion status when required components are present.
        
        Checks that the current VideoFile has a stored raw file, sensitive metadata, and video metadata; if all are present, sets any of the attributes `import_completed`, `processing_complete`, and `ready_for_validation` to True when they exist on the model and saves those fields. Logs a success message and which fields were updated. If the required components are missing, logs a warning. Any exceptions during this signaling are caught and logged.
        """
        try:
            video = self._require_current_video()

            raw_field: FieldFile | None = getattr(video, "raw_file", None)
            raw_exists = storage_file_exists(raw_field)

            video_processing_complete = video.sensitive_meta is not None and video.video_meta is not None and raw_exists

            if video_processing_complete:
                self.logger.info(
                    "Video %s processing completed successfully - ready for validation",
                    video.uuid,
                )

                # Update completion flags if they exist
                completion_fields = []
                for field_name in [
                    "import_completed",
                    "processing_complete",
                    "ready_for_validation",
                ]:
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
        """
        Ensure video state exists, attempt to restore the original raw file, and reset processing flags after a processing error.
        
        This routine verifies or creates a VideoState for the current video, tries to copy the stored raw file back to the original source location when available, and clears in-progress processing flags (frames_extracted, frames_initialized, video_meta_extracted, text_meta_extracted) if processing had been started. Side effects: may modify the VideoFile state, write a file to the original path, and persist changes to the VideoState.
        """
        if self.current_video and hasattr(self.current_video, "state"):
            if self.current_video.state is None:
                try:
                    self.current_video.get_or_create_state()
                except Exception as e:
                    self.logger.warning(f"Video state not found for video {self.current_video.uuid} during error cleanup {e}")
                    return
            self.current_video.state = self.current_video.get_or_create_state()
            try:
                if self.original_file_path is not None:
                    assert Path(self.original_file_path).exists()
                else:
                    self.logger.warning("Original file path is None")
                self.logger.info("Marked video import as failed in state")
                raw_file_path = getattr(self.current_video.raw_file, "path", None)
                original_file_path = self.original_file_path
                if raw_file_path and original_file_path:
                    shutil.copy2(str(raw_file_path), str(original_file_path))
                else:
                    self.logger.warning("Cannot restore original raw file: path is None")
            except AssertionError:
                self.logger.warning("Original file path does not exist")
            try:
                
                if not isinstance(self.current_video.state, VideoState):
                    logger.error("Current video is none after Assertion for Video File")
                    raise AssertionError
                    
                
                if self.processing_context.get("processing_started"):
                    self.current_video.state.frames_extracted = False
                    self.current_video.state.frames_initialized = False
                    self.current_video.state.video_meta_extracted = False
                    self.current_video.state.text_meta_extracted = False
                    self.current_video.state.save()
                
            except Exception as e:
                self.logger.warning(f"Error during cleanup: {e}")
            

    def _cleanup_processing_context(self):
        """
        Clean up the per-file processing context and release any acquired file lock.
        
        If a lock was acquired it will be released. If anonymization did not complete, the file is removed from the in-memory processed_files set. The method resets current_video and processing_context.
        """
        # DEFENSIVE: Ensure processing_context exists before accessing it
        if not hasattr(self, "processing_context"):
            self.processing_context = {}

        try:
            # Release file lock if it was acquired
            lock_context = self.processing_context.get("_lock_context")
            if lock_context is not None:
                try:
                    lock_context.__exit__(None, None, None)
                    self.logger.info("Released file lock")
                except Exception as e:
                    self.logger.warning(f"Error releasing file lock: {e}")

            # Remove file from processed set if processing failed
            file_path = self.processing_context.get("file_path")
            if file_path and not self.processing_context.get("anonymization_completed"):
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
    delete_source: bool = True,
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
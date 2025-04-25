import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Dict, List
import cv2
# --- Import SensitiveMeta class ---
from ...metadata import SensitiveMeta
# --- End Import ---
from ...metadata.sensitive_meta_logic import update_or_create_sensitive_meta_from_dict
from django.db import transaction

if TYPE_CHECKING:
    from .video_file import VideoFile

logger = logging.getLogger(__name__)

def _update_text_metadata(
    video: "VideoFile",
    ocr_frame_fraction: float = 0.01,
    cap: int = 10,
    overwrite: bool = False,
) -> Optional["SensitiveMeta"]:
    """
    Extracts text from a fraction of video frames, updates or creates SensitiveMeta,
    and potentially updates the VideoFile's date field. Requires frames to be extracted.

    State Transitions:
        - Pre-condition: Requires state.frames_extracted=True.
        - Post-condition: Sets state.sensitive_data_retrieved=True (even if no text found).
    """
    logger.debug(f"Updating text metadata for {video.uuid}")
    state = video.get_or_create_state()

    # --- Pre-condition Checks ---
    if not state.frames_extracted:
        logger.error("Cannot update text metadata for video %s: Frames not extracted.", video.uuid)
        return None # Indicate failure

    if state.sensitive_data_retrieved and not overwrite:
        logger.warning("Text already extracted for video %s and overwrite=False. Skipping.", video.uuid)
        return video.sensitive_meta # Return existing meta if available
    # --- End Pre-condition Checks ---


    # Extract text using the AI helper function
    # This function itself checks frames_extracted state again, which is slightly redundant but safe.
    extracted_data_dict = video.extract_text_from_frames(
        frame_fraction=ocr_frame_fraction, cap=cap
    )

    # --- Atomic Update Block ---
    try:
        with transaction.atomic():
            # Refresh state in case it changed
            state.refresh_from_db()
            sensitive_meta_instance = video.sensitive_meta # Get current instance

            if not extracted_data_dict:
                logger.warning("No text extracted for video %s; skipping SensitiveMeta update.", video.uuid)
                # Mark state as retrieved even if no data found, to avoid re-running unless overwrite=True
                if not state.sensitive_data_retrieved:
                    state.sensitive_data_retrieved = True
                    state.save(update_fields=['sensitive_data_retrieved'])
                return sensitive_meta_instance # Return existing meta if available

            # Add center info if not already present in extracted data
            if 'center_name' not in extracted_data_dict and video.center:
                extracted_data_dict['center_name'] = video.center.name
            logger.debug("Data for SensitiveMeta update: %s", extracted_data_dict)


            # Pass the Class, the data dict, and the current instance (or None)
            sensitive_meta, created = update_or_create_sensitive_meta_from_dict(
                SensitiveMeta, # Pass the class
                extracted_data_dict,
                instance=sensitive_meta_instance # Pass current instance via keyword
            )

            # Update VideoFile fields if necessary
            update_fields_video = []
            if created or sensitive_meta != sensitive_meta_instance: # Check if relation needs update
                video.sensitive_meta = sensitive_meta
                update_fields_video.append('sensitive_meta')

            if not video.date and sensitive_meta and extracted_data_dict.get('date'):
                extracted_date = extracted_data_dict.get('date')
                if extracted_date: # Ensure date is not None or empty
                    video.date = extracted_date
                    update_fields_video.append("date")

            # Save VideoFile if fields changed
            if update_fields_video:
                video.save(update_fields=update_fields_video)

            # Update state
            if not state.sensitive_data_retrieved:
                state.sensitive_data_retrieved = True
                state.save(update_fields=['sensitive_data_retrieved'])

            logger.debug("Successfully updated/created SensitiveMeta and state for video %s.", video.uuid)
            return sensitive_meta

    except Exception as e:
        logger.error("Failed to update/create SensitiveMeta or state for video %s: %s", video.uuid, e, exc_info=True)
        # State is not reset here on failure, transaction rollback handles consistency.
        return None # Indicate failure
    # --- End Atomic Update Block ---

def _update_video_meta(video: "VideoFile", save_instance: bool = True):
    """Updates or creates the technical VideoMeta from the raw video file."""
    from ...metadata import VideoMeta # Local import

    logger.debug("Updating technical VideoMeta for video %s (from raw file).", video.uuid)

    if not video.has_raw:
        logger.error("Raw video file path not available for %s. Cannot update VideoMeta.", video.uuid)
        return

    raw_video_path = video.get_raw_file_path() # Use helper
    if not raw_video_path or not raw_video_path.exists():
        logger.error("Raw video file path %s does not exist or is None. Cannot update VideoMeta.", raw_video_path)
        return

    try:
        vm = video.video_meta
        if vm:
            logger.info("Updating existing VideoMeta (PK: %s) for video %s.", vm.pk, video.uuid)
            vm.update_meta(raw_video_path) # Assuming this method exists
            vm.save()
        else:
            if not video.center or not video.processor:
                logger.error("Cannot create VideoMeta for %s: Center or Processor is missing.", video.uuid)

                return

            logger.info("Creating new VideoMeta for video %s.", video.uuid)
            # Assuming create_from_file exists and returns a saved instance or requires saving
            video.video_meta = VideoMeta.create_from_file(
                video_path=raw_video_path,
                center=video.center,
                processor=video.processor,
                save_instance=True # Let create_from_file handle saving
            )
            logger.info("Created and linked VideoMeta (PK: %s) for video %s.", video.video_meta.pk, video.uuid)

        # Save the VideoFile instance itself if requested and if video_meta was linked/updated
        if save_instance:
            update_fields = ["video_meta"]
            # Check if derived fields also need updating
            if video.video_meta:
                meta_fields = ["fps", "duration", "frame_count", "width", "height"]
                for field in meta_fields:
                    if getattr(video, field) is None and getattr(video.video_meta, field, None) is not None:
                        # No need to set attribute here, save method handles it
                        update_fields.append(field)
            video.save(update_fields=list(set(update_fields))) # Use set to avoid duplicates
            logger.info("Saved video %s after VideoMeta update.", video.uuid)

    except Exception as e:
        logger.error("Failed to update/create VideoMeta for video %s: %s", video.uuid, e, exc_info=True)



def _get_fps(video: "VideoFile") -> Optional[float]:
    """Gets the FPS, trying instance, VideoMeta, and finally direct file access."""
    if video.fps is not None:
        return video.fps

    logger.debug("FPS not set on instance %s, checking VideoMeta.", video.uuid)


    if not video.video_meta:
        logger.info("VideoMeta not linked for %s, attempting update.", video.uuid)

        _update_video_meta(video, save_instance=True) # Call the helper function

    # Check again after potential update
    if video.fps is not None:
        return video.fps
    elif video.video_meta and video.video_meta.fps is not None:
        logger.info("Retrieved FPS %.2f from VideoMeta for %s.", video.video_meta.fps, video.uuid)
        video.fps = video.video_meta.fps
        # Avoid saving if called from within the save method itself
        if not getattr(video, '_saving', False):
            video.save(update_fields=["fps"])
        return video.fps
    else:
        logger.warning("Could not determine FPS from VideoMeta for video %s. Trying direct raw file access.", video.uuid)
        try:
            if video.has_raw:
                video_path = video.get_raw_file_path() # Use helper
                if video_path and video_path.exists():
                    video_cap = cv2.VideoCapture(video_path.as_posix())
                    if video_cap.isOpened():
                        fps = video_cap.get(cv2.CAP_PROP_FPS)
                        video_cap.release()
                        if fps and fps > 0:
                            video.fps = fps
                            logger.info("Determined FPS %.2f directly from file for %s.", video.fps, video.uuid)
                            if not getattr(video, '_saving', False):
                                video.save(update_fields=["fps"])
                            return video.fps
                    else:
                        logger.warning("Could not open video file %s with OpenCV for FPS check.", video_path)
                elif video_path:
                    logger.warning("Raw file path %s does not exist for direct FPS check.", video_path)
                else:
                    logger.warning("Raw file path is None for direct FPS check.")
            else:
                logger.warning("Raw file not available for direct FPS check.")

        except Exception as e:
            logger.error("Error getting FPS directly from file %s: %s", video.raw_file.name if video.has_raw else 'N/A', e)

        logger.warning("Could not determine FPS for video %s.", video.uuid)

        return None


def _get_endo_roi(video: "VideoFile") -> Optional[Dict[str, int]]:
    """
    Gets the endoscope region of interest (ROI) dictionary from the linked VideoMeta.

    The ROI dictionary typically contains 'x', 'y', 'width', 'height'.
    Returns None if VideoMeta is not linked or ROI is not properly defined.
    """
    if not video.video_meta:
        logger.warning("VideoMeta not linked for video %s. Cannot get endo ROI.", video.uuid)
        # Optionally try to update VideoMeta here?
        # _update_video_meta(video, save_instance=True)
        # if not video.video_meta: return None # Return if still not available
        return None


    try:
        # Assuming VideoMeta has a method get_endo_roi()
        endo_roi = video.video_meta.get_endo_roi()
        # Basic validation
        if endo_roi and all(k in endo_roi for k in ["x", "y", "width", "height"]) and all(isinstance(v, int) for v in endo_roi.values()):
            logger.debug("Retrieved endo ROI for video %s: %s", video.uuid, endo_roi)
            return endo_roi
        else:
            logger.warning("Endo ROI not fully defined or invalid in VideoMeta for video %s. ROI: %s", video.uuid, endo_roi)
            return None
    except AttributeError:
        logger.error("VideoMeta object for video %s does not have a 'get_endo_roi' method.", video.uuid)
        return None
    except Exception as e:
        logger.error("Error getting endo ROI from VideoMeta for video %s: %s", video.uuid, e, exc_info=True)
        return None


def _get_crop_template(video: "VideoFile") -> Optional[List[int]]:
    """Generates a crop template [y1, y2, x1, x2] from the endo ROI."""
    endo_roi = _get_endo_roi(video) # Use the helper function
    if not endo_roi:
        logger.warning("Cannot generate crop template for video %s: Endo ROI not available.", video.uuid)
        return None

    x = endo_roi["x"]
    y = endo_roi["y"]
    width = endo_roi["width"]
    height = endo_roi["height"]

    # Validate dimensions
    if None in [x, y, width, height] or width <= 0 or height <= 0:
        logger.warning("Invalid ROI dimensions for video %s: %s", video.uuid, endo_roi)
        return None

    # Ensure crop boundaries are within image dimensions if available
    img_h, img_w = video.height, video.width
    if img_h and img_w:
        y1 = max(0, y)
        y2 = min(img_h, y + height)
        x1 = max(0, x)
        x2 = min(img_w, x + width)
        if y1 >= y2 or x1 >= x2:
            logger.warning("Calculated crop template has zero or negative size for video %s. ROI: %s, Img: %dx%d", video.uuid, endo_roi, img_w, img_h)
            return None
        crop_template = [y1, y2, x1, x2]
    else:
        # Proceed without boundary check if image dimensions unknown
        crop_template = [y, y + height, x, x + width]


    logger.debug("Generated crop template for video %s: %s", video.uuid, crop_template)
    return crop_template


def _initialize_video_specs(video: "VideoFile", use_raw: bool = True) -> bool:
    """Initializes basic video specs (fps, w, h, count, duration) directly from the video file."""
    video_path: Optional[Path] = None
    target_file_name: Optional[str] = None

    if use_raw and video.has_raw:
        video_path = video.get_raw_file_path() # Use IO helper
        target_file_name = video.raw_file.name
    elif video.active_file: # Fallback to active file if raw not requested or available
        video_path = video.active_file_path # Use property relying on IO helpers
        target_file_name = video.active_file.name
    else:
        logger.error("No suitable video file found for spec initialization for %s.", video.uuid)
        return False

    if not video_path:
        logger.error("Could not determine video file path for spec initialization for %s.", video.uuid)
        return False

    logger.info("Initializing video specs directly from file %s (%s) for %s", target_file_name, video_path, video.uuid)
    try:
        if not video_path.exists():
            logger.error("Video file not found at %s for spec initialization.", video_path)
            return False

        video_cap = cv2.VideoCapture(video_path.as_posix())
        if not video_cap.isOpened():
            logger.error("Could not open video file %s with OpenCV for spec initialization.", video_path)
            video_cap.release() # Ensure release even if not opened
            return False

        updated = False
        fields_to_update = []

        # Get current values before checking
        current_fps = video.fps
        current_width = video.width
        current_height = video.height
        current_frame_count = video.frame_count
        current_duration = video.duration

        # --- Get values from OpenCV ---
        try:
            file_fps = video_cap.get(cv2.CAP_PROP_FPS)
            file_width = int(video_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            file_height = int(video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            file_frame_count = int(video_cap.get(cv2.CAP_PROP_FRAME_COUNT))
        except Exception as cv_err:
            logger.error("Error getting properties from OpenCV for %s: %s", video_path, cv_err)
            video_cap.release()
            return False
        finally:
            video_cap.release() # Ensure release after getting props

        # --- Update FPS ---
        if current_fps is None and file_fps and file_fps > 0:
            video.fps = file_fps
            fields_to_update.append("fps")
            updated = True
            current_fps = file_fps # Update local var for duration calc

        # --- Update Width ---
        if current_width is None and file_width > 0:
            video.width = file_width
            fields_to_update.append("width")
            updated = True

        # --- Update Height ---
        if current_height is None and file_height > 0:
            video.height = file_height
            fields_to_update.append("height")
            updated = True

        # --- Update Frame Count and Duration ---
        # Only update if frame count is valid and FPS is known
        if file_frame_count > 0 and current_fps and current_fps > 0:
            if current_frame_count is None:
                video.frame_count = file_frame_count
                fields_to_update.append("frame_count")
                updated = True
            if current_duration is None:
                video.duration = file_frame_count / current_fps
                fields_to_update.append("duration")
                updated = True
        elif file_frame_count <= 0:
            logger.warning("Invalid frame count (%d) obtained from %s.", file_frame_count, video_path)
        elif not current_fps or current_fps <= 0:
            logger.warning("Cannot calculate duration for %s as FPS is unknown or invalid (%.2f).", video_path, current_fps or 0)


        # --- Save if updated ---
        if updated:
            logger.info("Updated video specs from file %s: %s", target_file_name, ", ".join(fields_to_update))
            video.save(update_fields=fields_to_update)
            return True
        else:
            logger.info("No video specs needed updating from file %s.", target_file_name)
            return True

    except Exception as e:
        logger.error("Error initializing video specs from file %s: %s", video_path, e, exc_info=True)
        # Ensure capture is released in case of unexpected error
        if 'video_cap' in locals() and video_cap.isOpened():
            video_cap.release()
        return False

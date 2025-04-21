import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Dict, List
import cv2
from icecream import ic

if TYPE_CHECKING:
    from .video_file import VideoFile
    from ...metadata import VideoMeta, SensitiveMeta

logger = logging.getLogger(__name__)

def _update_text_metadata(video: "VideoFile", ocr_frame_fraction=0.001, cap: int = 15, save_instance: bool = True):
    """Extracts text via OCR and updates/creates SensitiveMeta."""
    from ...metadata import SensitiveMeta # Local import

    logger.info("Updating text metadata via OCR for video %s.", video.uuid)
    ic(f"Updating text metadata for {video.uuid}") # Use uuid

    # Call the AI helper function for extraction
    extracted_data_dict = video.extract_text_information(
        frame_fraction=ocr_frame_fraction, cap=cap
    )

    if extracted_data_dict is None:
        logger.warning("No text extracted for video %s; skipping SensitiveMeta update.", video.uuid)
        ic("No text extracted; skipping metadata update.")
        return

    # Add center name if available
    if video.center:
        extracted_data_dict["center_name"] = video.center.name
    else:
        logger.warning("Center not set for video %s during text metadata update.", video.uuid)

    logger.debug("Data extracted for SensitiveMeta update: %s", extracted_data_dict)
    ic("Data for SensitiveMeta update:", extracted_data_dict)

    try:
        sm = video.sensitive_meta
        if sm:
            logger.info("Updating existing SensitiveMeta (PK: %s) for video %s.", sm.pk, video.uuid)
            ic(f"Updating existing SensitiveMeta {sm.pk}")
            sm.update_from_dict(extracted_data_dict) # Assuming this method exists
            sm.save()
        else:
            logger.info("Creating new SensitiveMeta for video %s.", video.uuid)
            ic("Creating new SensitiveMeta")
            # Assuming create_from_dict exists and returns a saved instance or requires saving
            video.sensitive_meta = SensitiveMeta.create_from_dict(extracted_data_dict)
            video.sensitive_meta.save() # Ensure it's saved

        # Update state
        state = video.get_or_create_state() # Use State helper
        state.sensitive_data_retrieved = True
        state.save(update_fields=["sensitive_data_retrieved"])
        logger.info("Successfully updated/created SensitiveMeta and set state for video %s.", video.uuid)

        # Save the VideoFile instance itself if requested and if sensitive_meta was linked
        if save_instance:
            update_fields = ["sensitive_meta"]
            # Check if derived fields also need updating
            if video.sensitive_meta:
                if not video.patient and video.sensitive_meta.pseudo_patient: update_fields.append("patient")
                if not video.examination and video.sensitive_meta.pseudo_examination: update_fields.append("examination")
                if not video.date and video.sensitive_meta.date: update_fields.append("date")
            video.save(update_fields=update_fields)
            logger.info("Saved video %s after metadata update.", video.uuid)

    except Exception as e:
        logger.error("Failed to update/create SensitiveMeta or state for video %s: %s", video.uuid, e, exc_info=True)
        ic(f"Failed to update/create SensitiveMeta or state: {e}")
        # Attempt to mark state as failed
        try:
            state = video.get_or_create_state() # Use State helper
            state.sensitive_data_retrieved = False
            state.save(update_fields=["sensitive_data_retrieved"])
        except Exception as state_e:
             logger.error("Failed to update state after SensitiveMeta error: %s", state_e)


def _update_video_meta(video: "VideoFile", save_instance: bool = True):
    """Updates or creates the technical VideoMeta from the raw video file."""
    from ...metadata import VideoMeta # Local import

    logger.info("Updating technical VideoMeta for video %s (from raw file).", video.uuid)
    ic(f"Updating VideoMeta for {video.uuid} from raw file")

    if not video.has_raw:
        logger.error("Raw video file path not available for %s. Cannot update VideoMeta.", video.uuid)
        ic("Raw video file missing, cannot update VideoMeta.")
        return

    raw_video_path = video.get_raw_file_path() # Use helper
    if not raw_video_path or not raw_video_path.exists():
        logger.error("Raw video file path %s does not exist or is None. Cannot update VideoMeta.", raw_video_path)
        ic(f"Raw video file path {raw_video_path} missing or None, cannot update VideoMeta.")
        return

    try:
        vm = video.video_meta
        if vm:
            logger.info("Updating existing VideoMeta (PK: %s) for video %s.", vm.pk, video.uuid)
            ic(f"Updating existing VideoMeta {vm.pk}")
            vm.update_meta(raw_video_path) # Assuming this method exists
            vm.save()
        else:
            if not video.center or not video.processor:
                 logger.error("Cannot create VideoMeta for %s: Center or Processor is missing.", video.uuid)
                 ic(f"Cannot create VideoMeta for {video.uuid}: Center or Processor missing.")
                 return

            logger.info("Creating new VideoMeta for video %s.", video.uuid)
            ic("Creating new VideoMeta")
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
        ic(f"Failed to update/create VideoMeta: {e}")


def _get_fps(video: "VideoFile") -> Optional[float]:
    """Gets the FPS, trying instance, VideoMeta, and finally direct file access."""
    if video.fps is not None:
        return video.fps

    logger.debug("FPS not set on instance %s, checking VideoMeta.", video.uuid)
    ic("FPS not set on instance, checking VideoMeta.")

    if not video.video_meta:
        logger.info("VideoMeta not linked for %s, attempting update.", video.uuid)
        ic("VideoMeta not linked, attempting update.")
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
        ic("Could not determine FPS.")
        return None


def _get_endo_roi(video: "VideoFile") -> Optional[Dict[str, int]]:
    """
    Gets the endoscope region of interest (ROI) dictionary from the linked VideoMeta.

    The ROI dictionary typically contains 'x', 'y', 'width', 'height'.
    Returns None if VideoMeta is not linked or ROI is not properly defined.
    """
    if not video.video_meta:
        logger.warning("VideoMeta not linked for video %s. Cannot get endo ROI.", video.uuid)
        ic("VideoMeta not linked, cannot get endo ROI.")
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
            ic(f"Endo ROI not fully defined or invalid in VideoMeta: {endo_roi}")
            return None
    except AttributeError:
        logger.error("VideoMeta object for video %s does not have a 'get_endo_roi' method.", video.uuid)
        ic("VideoMeta object missing 'get_endo_roi' method.")
        return None
    except Exception as e:
        logger.error("Error getting endo ROI from VideoMeta for video %s: %s", video.uuid, e, exc_info=True)
        ic(f"Error getting endo ROI from VideoMeta: {e}")
        return None


def _get_crop_template(video: "VideoFile") -> Optional[List[int]]:
    """Generates a crop template [y1, y2, x1, x2] from the endo ROI."""
    endo_roi = _get_endo_roi(video) # Use the helper function
    if not endo_roi:
        logger.warning("Cannot generate crop template for video %s: Endo ROI not available.", video.uuid)
        ic("Cannot generate crop template: Endo ROI not available.")
        return None

    x = endo_roi["x"]
    y = endo_roi["y"]
    width = endo_roi["width"]
    height = endo_roi["height"]

    # Validate dimensions
    if None in [x, y, width, height] or width <= 0 or height <= 0:
        logger.warning("Invalid ROI dimensions for video %s: %s", video.uuid, endo_roi)
        ic(f"Invalid ROI dimensions: {endo_roi}")
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
             ic(f"Invalid crop template size. ROI: {endo_roi}, Img: {img_w}x{img_h}")
             return None
        crop_template = [y1, y2, x1, x2]
    else:
         # Proceed without boundary check if image dimensions unknown
         crop_template = [y, y + height, x, x + width]


    logger.debug("Generated crop template for video %s: %s", video.uuid, crop_template)
    return crop_template


def _initialize_video_specs(video: "VideoFile", use_raw: bool = True) -> bool:
    """Initializes basic video specs (fps, w, h, count, duration) directly from the video file."""
    target_file = video.raw_file if use_raw and video.has_raw else video.active_file
    if not target_file or not target_file.name:
        logger.error("No suitable video file found for spec initialization for %s.", video.uuid)
        return False

    logger.info("Initializing video specs directly from file %s for %s", target_file.name, video.uuid)
    try:
        video_path = Path(target_file.path)
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
            logger.info("Updated video specs from file %s: %s", target_file.name, ", ".join(fields_to_update))
            video.save(update_fields=fields_to_update)
            return True
        else:
            logger.info("No video specs needed updating from file %s.", target_file.name)
            return True

    except Exception as e:
        logger.error("Error initializing video specs from file %s: %s", getattr(target_file, 'path', 'N/A'), e, exc_info=True)
        # Ensure capture is released in case of unexpected error
        if 'video_cap' in locals() and video_cap.isOpened():
            video_cap.release()
        return False

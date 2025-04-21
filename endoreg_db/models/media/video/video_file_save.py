import logging
from typing import TYPE_CHECKING
from icecream import ic

if TYPE_CHECKING:
    from .video_file import VideoFile

logger = logging.getLogger(__name__)

def _save_video_file(video: "VideoFile", *args, **kwargs):
    """Handles the saving logic for a VideoFile instance."""
    video._saving = True  # Set flag to prevent recursive saves within this method

    if not video.processor and video.center:
        logger.warning("Processor not set for video %s (Center: %s). Metadata/processing might be incomplete.",
                       video.uuid, video.center.name)

    # Ensure frame_dir is set
    if not video.frame_dir:
        # Assuming _set_frame_dir is imported or available in the VideoFile class context
        video.set_frame_dir() # Call the method on the instance

    raw_video_path_obj = video.get_raw_file_path() # Use helper if defined

    # Initialize VideoMeta if missing and possible
    if not video.video_meta and raw_video_path_obj and raw_video_path_obj.exists() and video.center and video.processor:
        from ...metadata import VideoMeta # Local import
        logger.info("VideoMeta not found for video %s. Attempting to create/initialize.", video.uuid)
        try:
            video.video_meta = VideoMeta.create_from_file(
                video_path=raw_video_path_obj,
                center=video.center,
                processor=video.processor,
                save_instance=True
            )
            logger.info("Created and linked VideoMeta (PK: %s) for video %s.", video.video_meta.pk, video.uuid)
            ic(f"Created VideoMeta {video.video_meta.pk}")
            # Ensure 'video_meta' is added to update_fields if it's being used
            update_fields = kwargs.get('update_fields', [])
            if 'video_meta' not in update_fields:
                update_fields.append('video_meta')
            kwargs['update_fields'] = update_fields
        except Exception as e:
            logger.error("Failed to create VideoMeta for video %s: %s", video.uuid, e, exc_info=True)
            ic(f"Failed to create VideoMeta: {e}")
    elif video.video_meta and raw_video_path_obj and raw_video_path_obj.exists():
        # Potentially update existing VideoMeta if needed, or just pass
        pass

    # Derive fields from SensitiveMeta
    sm = video.sensitive_meta
    if sm:
        updated_fields_sm = []
        if not video.patient and sm.pseudo_patient:
            video.patient = sm.pseudo_patient
            updated_fields_sm.append("patient")
            logger.debug("Derived patient %s from SensitiveMeta for video %s", video.patient.pk, video.uuid)
        if not video.examination and sm.pseudo_examination:
            video.examination = sm.pseudo_examination
            updated_fields_sm.append("examination")
            logger.debug("Derived examination %s from SensitiveMeta for video %s", video.examination.pk, video.uuid)
        if not video.date and sm.date:
            video.date = sm.date
            updated_fields_sm.append("date")
            logger.debug("Derived date %s from SensitiveMeta for video %s", video.date, video.uuid)
        if updated_fields_sm:
            update_fields = kwargs.get('update_fields', [])
            for field in updated_fields_sm:
                if field not in update_fields:
                    update_fields.append(field)
            kwargs['update_fields'] = update_fields

    # Derive fields from VideoMeta
    if video.video_meta:
        updated_fields_vm = []
        meta_fields = ["fps", "duration", "frame_count", "width", "height"]
        for field in meta_fields:
            meta_value = getattr(video.video_meta, field, None)
            # Check if the field on the video instance is None AND the meta_value is not None
            if getattr(video, field) is None and meta_value is not None:
                setattr(video, field, meta_value)
                updated_fields_vm.append(field)
        if updated_fields_vm:
            update_fields = kwargs.get('update_fields', [])
            for field in updated_fields_vm:
                if field not in update_fields:
                    update_fields.append(field)
            kwargs['update_fields'] = update_fields

    try:
        # Use 'super(type(video), video)' to call the parent's save method
        super(type(video), video).save(*args, **kwargs)
        logger.debug("Successfully saved VideoFile instance PK %s (UUID: %s)", video.pk, video.uuid)

        # Ensure state exists after saving
        if not video.state:
            # Assuming _get_or_create_state is imported or available
            video.get_or_create_state() # Call the method on the instance

    except Exception as e:
        logger.error("Error during super().save() for VideoFile PK %s: %s", video.pk, e, exc_info=True)
        raise
    finally:
        del video._saving # Remove the flag

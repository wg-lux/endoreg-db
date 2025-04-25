import logging
from typing import TYPE_CHECKING, Optional, Dict, List, Tuple
from django.db import transaction

# Added imports

# Configure logging
logger = logging.getLogger(__name__) # Changed from "video_file"

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile


    # --- Pipeline 1 ---
def _pipe_1(
    video_file:"VideoFile",
    model_name: str,
    model_meta_version: Optional[int] = None,
    delete_frames_after: bool = False,
    ocr_frame_fraction: float = 0.001,
    ocr_cap: int = 10,
    smooth_window_size_s: int = 1,
    binarize_threshold: float = 0.5,
    test_run: bool = False,
    n_test_frames: int = 10,
) -> bool:
    """
    Pipeline 1: Extract frames, text, predict, create segments, optionally delete frames.

    State Transitions:
        - Calls extract_frames (sets frames_extracted, frames_initialized).
        - Calls update_text_metadata (sets sensitive_data_retrieved).
        - Calls predict_video (requires frames_extracted).
        - Sets initial_prediction_completed=True, lvs_created=True.
        - Optionally calls delete_frames (resets frames_extracted, frames_initialized).
    """
    from .video_file_segments import _convert_sequences_to_db_segments # Added import
    from ...metadata import ModelMeta, VideoPredictionMeta
    from endoreg_db.models import AiModel, LabelVideoSegment

    logger.info(f"Starting Pipe 1 for video {video_file.uuid}")
    try:
        with transaction.atomic():
            # Ensure state exists before starting
            state = video_file.get_or_create_state()

            # Update VideoMeta if needed (can be done early in transaction)
            video_file.update_video_meta()

            # 1. Extract Frames (handles its own state checks and updates)
            logger.info("Pipe 1: Extracting frames...")
            if not video_file.extract_frames(overwrite=False):
                logger.error("Pipe 1 failed: Frame extraction method returned False.")
                return False # Transaction will rollback

            logger.info("Pipe 1: Frame extraction complete.")

            # 2. Extract Text Data (handles its own state checks and updates)
            logger.info("Pipe 1: Extracting text metadata...")
            video_file.update_text_metadata(
                ocr_frame_fraction=ocr_frame_fraction, cap=ocr_cap, overwrite=False
            )

            logger.info("Pipe 1: Text metadata extraction complete.")

            # 3. Perform Initial Prediction
            logger.info(f"Pipe 1: Performing prediction with model '{model_name}'...")
            try:
                ai_model_obj = AiModel.objects.get(name=model_name)
                if model_meta_version is not None:
                    model_meta = ai_model_obj.metadata_versions.get(version=model_meta_version)
                else:
                    model_meta = ai_model_obj.get_latest_version()
            except AiModel.DoesNotExist:
                logger.error(f"Pipe 1 failed: Model '{model_name}' not found.")
                return False
            except ModelMeta.DoesNotExist:
                logger.error(
                    f"Pipe 1 failed: ModelMeta version {model_meta_version} for model '{model_name}' not found."
                )
                return False

            sequences: Optional[Dict[str, List[Tuple[int, int]]]] = video_file.predict_video(
                model_meta=model_meta,
                smooth_window_size_s=smooth_window_size_s,
                binarize_threshold=binarize_threshold,
                test_run=test_run,
                n_test_frames=n_test_frames,
            )

            if sequences is None:
                logger.error("Pipe 1 failed: Prediction pipeline returned None.")
                return False # Transaction will rollback
            logger.info("Pipe 1: Prediction complete.")

            # --- Set Prediction State ---
            state.refresh_from_db()
            if not state.initial_prediction_completed:
                state.initial_prediction_completed = True
                state.save(update_fields=['initial_prediction_completed'])
                logger.info("Pipe 1: Set initial_prediction_completed state to True.")

            logger.info(f"Pipe 1: Sequences returned from prediction: {sequences}")
            if not sequences:
                logger.warning("Pipe 1: Prediction returned empty sequences dictionary. No LabelVideoSegments will be created.")

            # 4. Create LabelVideoSegments
            logger.info("Pipe 1: Creating LabelVideoSegments from predictions...")
            try:
                video_prediction_meta, created = VideoPredictionMeta.objects.get_or_create(
                    video_file=video_file, model_meta=model_meta
                )
                if created:
                     logger.warning("Pipe 1: VideoPredictionMeta was not created during predict_video call. Created now.")

                logger.info(f"Pipe 1: Calling _convert_sequences_to_db_segments for video {video_file.uuid} with prediction meta {video_prediction_meta.pk}")
                _convert_sequences_to_db_segments(
                    video=video_file,
                    sequences=sequences,
                    video_prediction_meta=video_prediction_meta,
                )
                video_file.sequences = sequences
                video_file.save(update_fields=['sequences'])

                # --- Set LVS Created State ---
                state.refresh_from_db()
                if not state.lvs_created:
                    state.lvs_created = True
                    state.save(update_fields=['lvs_created'])
                    logger.info("Pipe 1: Set lvs_created state to True.")

                logger.info("Pipe 1: LabelVideoSegment creation complete.")
                lvs_count_after = LabelVideoSegment.objects.filter(video_file=video_file).count()
                logger.info(f"Pipe 1: Found {lvs_count_after} LabelVideoSegments after conversion attempt.")
            except Exception as seg_e:
                logger.error(f"Pipe 1 failed during LabelVideoSegment creation: {seg_e}", exc_info=True)
                return False # Transaction will rollback

        logger.info(f"Pipe 1 completed successfully for video {video_file.uuid}")
        if delete_frames_after:
            logger.info("Pipe 1: Deleting frames after processing...")
            try:
                video_file.delete_frames()
                logger.info("Pipe 1: Frame deletion complete.")
            except Exception as del_e:
                logger.error(f"Pipe 1: Error during optional frame deletion: {del_e}", exc_info=True)
        else:
            logger.info("Pipe 1: Frame deletion skipped.")

        return True

    except Exception as e:
        logger.error(f"Pipe 1 failed for video {video_file.uuid} within atomic block: {e}", exc_info=True)
        return False

# --- Test after Pipe 1 ---
def _test_after_pipe_1(video_file:"VideoFile", start_frame: int = 0, end_frame: int = 100) -> bool:
    """
    Simulates human annotation validation after Pipe 1.
    Creates 'outside' segments, marks them as validated, and marks sensitive meta as verified.
    """
    from ...label import LabelVideoSegment, Label
    from ...state import LabelVideoSegmentState # Import the state model

    logger.info(f"Starting _test_after_pipe_1 for video {video_file.uuid}")
    try:
        with transaction.atomic(): # Wrap in transaction for atomicity
            # 1. Create 'outside' LabelVideoSegments
            try:
                outside_label = Label.objects.get(name__iexact="outside")
                logger.info(f"Creating 'outside' annotation segment [{start_frame}-{end_frame}]")
                # Create the segment
                segment = LabelVideoSegment.objects.create(
                    video_file=video_file,
                    label=outside_label,
                    start_frame_number=start_frame,
                    end_frame_number=end_frame,
                    prediction_meta=None, # Assuming it's a manual annotation for test
                )
                # Get or create the state for the segment using the correct reverse relation name
                segment_state, created = LabelVideoSegmentState.objects.get_or_create(origin=segment)
                # Mark the segment state as validated
                segment_state.is_validated = True
                segment_state.save(update_fields=['is_validated'])
                logger.info(f"Marked created 'outside' segment (PK: {segment.pk}) as validated.")

            except Label.DoesNotExist:
                logger.error("_test_after_pipe_1 failed: 'outside' Label not found.")
                return False
            except Exception as e:
                logger.error(f"_test_after_pipe_1 failed during segment creation or validation: {e}", exc_info=True)
                return False

            # 2. Set Sensitive Metadata state to verified
            if video_file.sensitive_meta:
                sm = video_file.sensitive_meta
                # Ensure sensitive meta state exists before trying to update
                sm_state = sm.get_or_create_state() # Use the state helper method
                logger.info("Setting sensitive meta state to verified.")
                sm_state.dob_verified = True
                sm_state.names_verified = True
                sm_state.save(update_fields=['dob_verified', 'names_verified']) # Use update_fields
                logger.info("Sensitive meta state updated.")
            else:
                logger.warning("_test_after_pipe_1: No sensitive meta found to verify.")

        logger.info(f"_test_after_pipe_1 completed successfully for video {video_file.uuid}")
        return True

    except Exception as e:
        logger.error(f"_test_after_pipe_1 failed for video {video_file.uuid}: {e}", exc_info=True)
        return False

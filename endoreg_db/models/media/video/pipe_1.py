import logging
from typing import TYPE_CHECKING, Optional, Dict, List, Tuple
from django.db import transaction

# Added imports
from .video_file_segments import _convert_sequences_to_db_segments
# --- Import necessary models for type hints and operations ---
from ...metadata import ModelMeta, VideoPredictionMeta
from ...state import VideoState
from ...label import LabelVideoSegment
from ...administration.ai import AiModel
# --- End Imports ---

# Configure logging
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile


# --- Helper Step Functions for Pipe 1 ---

def _pipe_1_step_ensure_state(video_file: "VideoFile") -> "VideoState":
    """Ensures the VideoState object exists."""
    logger.debug("Pipe 1 Step: Ensuring state exists for video %s...", video_file.uuid)
    state = video_file.get_or_create_state()
    logger.debug("Pipe 1 Step: State ensured for video %s.", video_file.uuid)
    return state

def _pipe_1_step_update_meta(video_file: "VideoFile"):
    """Updates the VideoMeta object."""
    logger.info("Pipe 1 Step: Updating video meta for video %s...", video_file.uuid)
    video_file.update_video_meta() # Raises exceptions on failure
    logger.info("Pipe 1 Step: Video meta update complete for video %s.", video_file.uuid)

def _pipe_1_step_extract_frames(video_file: "VideoFile"):
    """Extracts frames if not already done."""
    logger.info("Pipe 1 Step: Extracting frames for video %s...", video_file.uuid)
    video_file.extract_frames(overwrite=False) # Raises exceptions on failure
    logger.info("Pipe 1 Step: Frame extraction complete for video %s.", video_file.uuid)

def _pipe_1_step_extract_text(video_file: "VideoFile", ocr_frame_fraction: float, ocr_cap: int):
    """Extracts text metadata."""
    logger.info("Pipe 1 Step: Extracting text metadata for video %s...", video_file.uuid)
    video_file.update_text_metadata(
        ocr_frame_fraction=ocr_frame_fraction, cap=ocr_cap, overwrite=False
    ) # Raises exceptions on failure
    logger.info("Pipe 1 Step: Text metadata extraction complete for video %s.", video_file.uuid)

def _pipe_1_step_predict(
    video_file: "VideoFile",
    model_name: str,
    model_meta_version: Optional[int],
    smooth_window_size_s: int,
    binarize_threshold: float,
    test_run: bool,
    n_test_frames: int,
) -> Tuple[Dict[str, List[Tuple[int, int]]], "ModelMeta"]:
    """Performs prediction and sets the initial prediction state."""
    logger.info(f"Pipe 1 Step: Performing prediction for video {video_file.uuid} with model '{model_name}'...")
    sequences, model_meta = video_file.predict_video(
        model_name=model_name, # Corrected argument name
        model_meta_version=model_meta_version,
        smooth_window_size_s=smooth_window_size_s,
        binarize_threshold=binarize_threshold,
        test_run=test_run,
        n_test_frames=n_test_frames,
    ) # Raises exceptions on failure
    logger.info("Pipe 1 Step: Prediction complete for video %s using model %s (Version: %s).", video_file.uuid, model_meta.name, model_meta.version)

    # Set Prediction State
    state = video_file.get_or_create_state() # Re-fetch state just in case
    if not state.initial_prediction_completed:
        state.initial_prediction_completed = True
        state.save(update_fields=['initial_prediction_completed'])
        logger.info("Pipe 1 Step: Set initial_prediction_completed state to True for video %s.", video_file.uuid)

    return sequences, model_meta

def _pipe_1_step_create_segments(
    video_file: "VideoFile",
    sequences: Dict[str, List[Tuple[int, int]]],
    model_meta: "ModelMeta",
):
    """Creates LabelVideoSegments from prediction sequences and sets LVS state."""
    logger.info("Pipe 1 Step: Creating LabelVideoSegments for video %s from predictions...", video_file.uuid)

    if not sequences:
        logger.warning("Pipe 1 Step: Prediction returned empty sequences dictionary for video %s. No LabelVideoSegments will be created.", video_file.uuid)
        # Still set LVS state to True as the prediction step completed
        state = video_file.get_or_create_state()
        if not state.lvs_created:
            state.lvs_created = True
            state.save(update_fields=['lvs_created'])
            logger.info("Pipe 1 Step: Set lvs_created state to True for video %s (no sequences).", video_file.uuid)
        return # Exit early if no sequences

    try:
        # Get or create VideoPredictionMeta (should ideally exist from predict step)
        video_prediction_meta, created = VideoPredictionMeta.objects.get_or_create(
            video_file=video_file, model_meta=model_meta
        )
        if created:
            logger.warning("Pipe 1 Step: VideoPredictionMeta was not created during predict_video call. Created now.")

        logger.info(f"Pipe 1 Step: Calling _convert_sequences_to_db_segments for video {video_file.uuid} with prediction meta {video_prediction_meta.pk}")
        _convert_sequences_to_db_segments(
            video=video_file,
            sequences=sequences,
            video_prediction_meta=video_prediction_meta,
        )
        # Save sequences to the VideoFile model field
        video_file.sequences = sequences
        video_file.save(update_fields=['sequences'])

        # Set LVS Created State
        state = video_file.get_or_create_state() # Re-fetch state
        if not state.lvs_created:
            state.lvs_created = True
            state.save(update_fields=['lvs_created'])
            logger.info("Pipe 1 Step: Set lvs_created state to True for video %s.", video_file.uuid)

        logger.info("Pipe 1 Step: LabelVideoSegment creation step complete for video %s.", video_file.uuid)
        lvs_count_after = LabelVideoSegment.objects.filter(video_file=video_file).count()
        logger.info(f"Pipe 1 Step: Found {lvs_count_after} LabelVideoSegments for video {video_file.uuid} after conversion attempt.", )
    except Exception as seg_e:
        logger.error(f"Pipe 1 Step failed during LabelVideoSegment creation for video {video_file.uuid}: {seg_e}", exc_info=True)
        raise # Re-raise to ensure transaction rollback

# --- Main Pipeline 1 Function ---
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
    Orchestrates calls to step-based helper functions within a transaction.
    Returns True on success, False on failure (logs error).
    """
    logger.info(f"Starting Pipe 1 for video {video_file.uuid}")
    try:
        with transaction.atomic():
            # --- Call Step Functions ---
            _pipe_1_step_ensure_state(video_file)
            _pipe_1_step_update_meta(video_file)
            _pipe_1_step_extract_frames(video_file)
            _pipe_1_step_extract_text(video_file, ocr_frame_fraction, ocr_cap)
            sequences, model_meta = _pipe_1_step_predict(
                video_file, model_name, model_meta_version, smooth_window_size_s,
                binarize_threshold, test_run, n_test_frames
            )
            _pipe_1_step_create_segments(video_file, sequences, model_meta)
            # --- End Step Functions ---

        # --- Transaction committed successfully here ---
        logger.info(f"Pipe 1 completed successfully for video {video_file.uuid}")

        # Optional frame deletion happens outside the main transaction
        if delete_frames_after:
            logger.info("Pipe 1: Deleting frames after processing for video %s...", video_file.uuid)
            try:
                video_file.delete_frames() # Raises on state update failure
                logger.info("Pipe 1: Frame deletion complete for video %s.", video_file.uuid)
            except Exception as del_e:
                # Log error but don't change overall success status of Pipe 1
                logger.error(f"Pipe 1: Error during optional frame deletion for video {video_file.uuid}: {del_e}", exc_info=True)
        else:
            logger.info("Pipe 1: Frame deletion skipped for video %s.", video_file.uuid)

        return True # Overall success

    except (FileNotFoundError, ValueError, RuntimeError, ImportError, ModelMeta.DoesNotExist, AiModel.DoesNotExist, Exception) as e:
        # Catch specific exceptions raised by steps, plus general Exception
        logger.error(f"Pipe 1 failed for video {video_file.uuid}: {e}", exc_info=True)
        # Transaction automatically rolled back due to exception
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
                segment_state, _created = LabelVideoSegmentState.objects.get_or_create(origin=segment)
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

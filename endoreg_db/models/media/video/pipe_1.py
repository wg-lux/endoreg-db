import logging
from typing import TYPE_CHECKING, Optional, Dict, List, Tuple

# Added imports

# Configure logging
logger = logging.getLogger("video_file")

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
    """
    from .video_file_segments import _convert_sequences_to_db_segments # Added import
    from ...metadata import ModelMeta, VideoPredictionMeta 
    from endoreg_db.models import AiModel, LabelVideoSegment

    video_file.refresh_from_db()
    video_file.update_video_meta()

    logger.info(f"Starting Pipe 1 for video {video_file.uuid}")
    try:
        # 1. Extract Frames
        logger.info("Pipe 1: Extracting frames...")
        video_file.extract_frames(overwrite=False) # Avoid overwriting if already extracted
        state = video_file.get_or_create_state()
        if not state.frames_extracted:
            logger.error("Pipe 1 failed: Frame extraction did not complete successfully.")
            return False
        logger.info("Pipe 1: Frame extraction complete.")

        # 2. Extract Text Data
        logger.info("Pipe 1: Extracting text metadata...")
        video_file.update_text_metadata(
            ocr_frame_fraction=ocr_frame_fraction, cap=ocr_cap, overwrite=False
        )
        # Check state if needed, update_text_metadata handles its own logging
        logger.info("Pipe 1: Text metadata extraction complete.")

        # 3. Perform Initial Prediction
        logger.info(f"Pipe 1: Performing prediction with model '{model_name}'...")
        try:
            ai_model_obj = AiModel.objects.get(name=model_name)
            model_meta = ai_model_obj.get_latest_version()
        except ModelMeta.DoesNotExist:
            logger.error(f"Pipe 1 failed: ModelMeta '{model_name}' (Version: {model_meta_version}) not found.")
            return False

        sequences: Optional[Dict[str, List[Tuple[int, int]]]] = video_file.predict_video(
            model_meta=model_meta,
            smooth_window_size_s=smooth_window_size_s,
            binarize_threshold=binarize_threshold,
            test_run=test_run,
            n_test_frames=n_test_frames,
        )

        # After prediction:
        if sequences is None:
            logger.error("Pipe 1 failed: Prediction pipeline returned None.")
            return False
        logger.info("Pipe 1: Prediction complete.")
        # --- Set and Save State ---
        state.initial_prediction_completed = True
        state.save(update_fields=['initial_prediction_completed'])
        logger.info("Pipe 1: Set initial_prediction_completed state to True.")
        # --- End State Update --
        # --- Add logging for sequences ---
        logger.info(f"Pipe 1: Sequences returned from prediction: {sequences}")
        if not sequences:
             logger.warning("Pipe 1: Prediction returned empty sequences dictionary. No LabelVideoSegments will be created.")
        # --- End logging ---


        # 4. Create LabelVideoSegments
        logger.info("Pipe 1: Creating LabelVideoSegments from predictions...")
        try:
            # Get the prediction meta object created/retrieved within predict_video
            video_prediction_meta = VideoPredictionMeta.objects.get(
                video_file=video_file, model_meta=model_meta
            )
            # --- Add logging before conversion ---
            logger.info(f"Pipe 1: Calling _convert_sequences_to_db_segments for video {video_file.uuid} with prediction meta {video_prediction_meta.pk}")
            # --- End logging ---
            _convert_sequences_to_db_segments(
                video=video_file,
                sequences=sequences,
                video_prediction_meta=video_prediction_meta,
            )
            # Store sequences on the video object itself as well?
            video_file.sequences = sequences
            video_file.save(update_fields=['sequences'])
            # --- Set state.lvs_created ---
            state.lvs_created = True
            state.save(update_fields=['lvs_created'])
            logger.info("Pipe 1: Set lvs_created state to True.")
            # --- End state update ---
            logger.info("Pipe 1: LabelVideoSegment creation complete.")
            # --- Add logging after conversion attempt ---
            lvs_count_after = LabelVideoSegment.objects.filter(video_file=video_file).count()
            logger.info(f"Pipe 1: Found {lvs_count_after} LabelVideoSegments after conversion attempt.")
            # --- End logging ---
        except VideoPredictionMeta.DoesNotExist:
                logger.error("Pipe 1 failed: Could not find VideoPredictionMeta after prediction.")
                return False
        except Exception as e:
            logger.error(f"Pipe 1 failed during LabelVideoSegment creation: {e}", exc_info=True)
            return False
        
        # --- Add explicit return True ---
        logger.info(f"Pipe 1 completed successfully for video {video_file.uuid}")
        return True
        # --- End Add ---

    except Exception as e:
        logger.error(f"Pipe 1 failed for video {video_file.uuid}: {e}", exc_info=True)
        return False
    finally:
        # 5. Optionally delete frames
        if delete_frames_after:
            logger.info("Pipe 1: Deleting frames after processing...")
            try:
                video_file.delete_frames()
                logger.info("Pipe 1: Frame deletion complete.")
            except Exception as e:
                logger.error(f"Pipe 1 failed during frame deletion: {e}", exc_info=True)
        else:
            logger.info("Pipe 1: Frame deletion skipped.")
            
# --- Test after Pipe 1 ---
def _test_after_pipe_1(video_file:"VideoFile", start_frame: int = 0, end_frame: int = 100) -> bool:
    """
    Simulates human annotation validation after Pipe 1.
    Creates 'outside' segments and marks sensitive meta as verified.
    """
    from ...label import LabelVideoSegment, Label
    logger.info(f"Starting _test_after_pipe_1 for video {video_file.uuid}")
    try:
        # 1. Create 'outside' LabelVideoSegments
        try:
            outside_label = Label.objects.get(name__iexact="outside")
            logger.info(f"Creating 'outside' annotation segment [{start_frame}-{end_frame}]")
            # Create a segment - assuming custom_create handles saving
            LabelVideoSegment.objects.create(
                video_file=video_file,
                label=outside_label,
                start_frame_number=start_frame,
                end_frame_number=end_frame,
                prediction_meta=None, 
            )
        except Label.DoesNotExist:
            logger.error("_test_after_pipe_1 failed: 'outside' Label not found.")
            return False
        except Exception as e:
            logger.error(f"_test_after_pipe_1 failed during segment creation: {e}", exc_info=True)
            return False

        # 2. Set Sensitive Metadata state to verified
        if video_file.sensitive_meta:
            sm = video_file.sensitive_meta
            st_state = sm.state
            assert st_state, "SensitiveMeta state is None. Cannot set to verified."
            logger.info("Setting sensitive meta state to verified.")

            # Example: using a boolean field
            video_file.sensitive_meta.state.dob_verified = True
            video_file.sensitive_meta.state.names_verified = True
            video_file.sensitive_meta.state.save() # Save the SensitiveMeta instance
            logger.info("Sensitive meta state updated.")

        else:
            logger.warning("_test_after_pipe_1: No sensitive meta found to verify.")

        logger.info(f"_test_after_pipe_1 completed successfully for video {video_file.uuid}")
        return True

    except Exception as e:
        logger.error(f"_test_after_pipe_1 failed for video {video_file.uuid}: {e}", exc_info=True)
        return False

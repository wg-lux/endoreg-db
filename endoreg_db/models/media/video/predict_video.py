from typing import TYPE_CHECKING, Optional
from ...utils import TEST_RUN, TEST_RUN_FRAME_NUMBER
from icecream import ic
import logging
from .helpers import _predict_video_pipeline, _convert_sequences_to_db_segments

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .video_file import VideoFile
    from ...metadata import ModelMeta, VideoPredictionMeta

def _predict_video(
    video: "VideoFile",
    model_meta_name: str,
    model_meta_version: Optional[int] = None,
    dataset_name: str = "inference_dataset",
    smooth_window_size_s: int = 1,
    binarize_threshold: float = 0.5,
    test_run: bool = TEST_RUN,
    n_test_frames: int = TEST_RUN_FRAME_NUMBER,
    save_results: bool = True,
):
    """Helper function to run prediction pipeline and optionally save results."""
    from ...metadata import ModelMeta, VideoPredictionMeta

    try:
        model_meta = ModelMeta.get_by_name(model_meta_name, model_meta_version)
        logger.info("Using ModelMeta: %s (Version: %s)", model_meta.name, model_meta.version)
    except ModelMeta.DoesNotExist:
        logger.error("ModelMeta '%s' (Version: %s) not found.", model_meta_name, model_meta_version)
        raise

    state = video.get_or_create_state()

    if not state.frames_extracted:
        logger.error("Frames not extracted (state check) for %s, prediction aborted.", video.uuid)
        ic(f"Frames not extracted (state check) for {video.uuid}, prediction aborted.")
        return None

    predicted_sequences = _predict_video_pipeline(
        video=video,
        model_meta=model_meta,
        dataset_name=dataset_name,
        smooth_window_size_s=smooth_window_size_s,
        binarize_threshold=binarize_threshold,
        test_run=test_run,
        n_test_frames=n_test_frames,
    )

    if predicted_sequences is None:
        logger.error("Prediction pipeline failed or returned no sequences for video %s.", video.uuid)
        return None

    logger.info("Prediction pipeline completed successfully for video %s.", video.uuid)

    if save_results:
        logger.info("Saving prediction results for video %s...", video.uuid)
        try:
            video_prediction_meta = VideoPredictionMeta.objects.get(
                video_file=video, model_meta=model_meta
            )

            video.sequences = predicted_sequences
            video.ai_model_meta = model_meta

            update_fields = [
                "sequences",
                "ai_model_meta",
            ]
            video.save(update_fields=update_fields)
            logger.debug("Saved sequences and ai_model_meta to VideoFile %s.", video.uuid)

            state.initial_prediction_completed = True
            state.save(update_fields=["initial_prediction_completed"])
            logger.debug("Updated state.initial_prediction_completed for video %s.", video.uuid)

            logger.info("Converting sequences to LabelVideoSegments for video %s.", video.uuid)
            _convert_sequences_to_db_segments(
                video=video,
                sequences=predicted_sequences,
                video_prediction_meta=video_prediction_meta,
            )
            state.lvs_created = True
            state.save(update_fields=["lvs_created"])
            logger.info("Successfully saved results and created segments for video %s.", video.uuid)

        except VideoPredictionMeta.DoesNotExist:
            logger.error("VideoPredictionMeta not found after pipeline for video %s, model %s. Cannot save results.",
                         video.uuid, model_meta.name)
            state.initial_prediction_completed = False
            state.lvs_created = False
            state.save(update_fields=["initial_prediction_completed", "lvs_created"])
        except Exception as e:
            logger.error("Failed to save prediction results/segments for video %s: %s", video.uuid, e, exc_info=True)
            ic(f"Failed to save prediction results/segments: {e}")
            state.initial_prediction_completed = False
            state.lvs_created = False
            state.save(update_fields=["initial_prediction_completed", "lvs_created"])
    else:
        logger.info("Skipping saving of prediction results for video %s.", video.uuid)

    return predicted_sequences

from endoreg_db.models import VideoPredictionMeta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile, VideoState
    from .test_video_file_extracted import VideoFileModelExtractedTest

def _test_pipe_1(test:"VideoFileModelExtractedTest"):
    """
    Test the pipeline of the video file.
    This includes:
    - Pre-validation processing (pipe_1)
    - Simulating human validation processing (test_after_pipe_1)
    - Post-validation processing (pipe_2)
    """

    video_file:"VideoFile" = test.video_file

    success = video_file.pipe_1(
        model_name = test.ai_model_meta.model.name,
        delete_frames_after = True,
        ocr_frame_fraction=0.01,
        ocr_cap = 5
    )

    test.assertTrue(success, "Pipe 1 failed: Pre-Validation Processing failed.")

     # --- Assertions after Pipe 1 ---
    video_file.refresh_from_db()
    state = video_file.state # Access the related state object
    test.assertIsNotNone(state, "VideoState should exist after pipe_1")
    state.refresh_from_db() # Ensure state is up-to-date

    # Check if this is a mock object
    is_mock = hasattr(video_file, '__class__') and 'Mock' in video_file.__class__.__name__

    if not is_mock:
        # Only perform database queries for real VideoFile objects
        # Check Metadata objects
        test.assertIsNotNone(video_file.video_meta, "VideoMeta should exist after pipe_1")
        test.assertIsNotNone(video_file.sensitive_meta, "SensitiveMeta should exist after pipe_1")

        # Check Prediction Meta
        prediction_meta_exists = VideoPredictionMeta.objects.filter(
            video_file=video_file, model_meta=test.ai_model_meta
        ).exists()
        test.assertTrue(prediction_meta_exists, "VideoPredictionMeta should exist after pipe_1")

    # Check State flags - these work for both real and mock objects
    test.assertTrue(state.text_meta_extracted, "State.text_meta_extracted should be True")
    test.assertTrue(state.initial_prediction_completed, "State.initial_prediction_completed should be True")
    test.assertTrue(state.lvs_created,  "State.lvs_created should be True")
    # Frames should be deleted because delete_frames_after=True
    test.assertFalse(state.frames_extracted, "State.frames_extracted should be False after pipe_1 (delete_frames_after=True)")
    test.assertTrue(state.frames_initialized, "State.frames_initialized should be True after pipe_1 (delete_frames_after=True)")

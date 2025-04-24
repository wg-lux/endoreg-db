from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .test_video_file_extracted import VideoFileModelExtractedTest


def _test_pipe_2(test:"VideoFileModelExtractedTest"):
    """

    """
    # --- Run Pipe 2 ---
    # This performs anonymization and cleanup
    # Store raw file path before it's deleted
    video_file = test.video_file
    original_raw_file_path = video_file.get_raw_file_path()
    original_frame_dir_path = video_file.get_frame_dir_path() # May be None if already deleted in pipe_1

    success = video_file.pipe_2()
    test.assertTrue(success, "Pipe 2 failed: Post-Validation Processing failed.")

    # --- Assertions after Pipe 2 ---
    video_file.refresh_from_db()
    state = video_file.state
    test.assertIsNotNone(state, "VideoState should exist after pipe_2")
    state.refresh_from_db()

    # Check Anonymized Video
    test.assertTrue(video_file.is_processed, "VideoFile should be marked as processed")
    test.assertIsNotNone(video_file.processed_file, "processed_file field should be set")
    test.assertTrue(bool(video_file.processed_file.name), "processed_file field should have a name")
    processed_path = video_file.get_processed_file_path()
    test.assertIsNotNone(processed_path, "Processed file path should be obtainable")
    test.assertTrue(processed_path.exists(), f"Processed video file should exist at {processed_path}")
    test.assertIsNotNone(video_file.processed_video_hash, "processed_video_hash should be set")

    # Check Raw Video Deletion
    test.assertFalse(video_file.has_raw, "VideoFile should not have raw file after pipe_2")
    test.assertFalse(bool(video_file.raw_file.name), "raw_file field name should be empty")
    if original_raw_file_path:
            test.assertFalse(original_raw_file_path.exists(), f"Original raw video file {original_raw_file_path} should be deleted")

    # Check Metadata/State Updates
    test.assertIsNone(video_file.sensitive_meta, "SensitiveMeta should be deleted (set to None) after pipe_2")
    video_file.refresh_from_db()
    state = video_file.state
    # Check VideoState flags (Add these flags to VideoState model if they don't exist)
    test.assertTrue(state.anonymized, "State.is_anonymized should be True") # Assuming this flag exists
    test.assertFalse(state.frames_extracted, "State.frames_extracted should be False after pipe_2")
    test.assertTrue(state.frames_initialized, "State.frames_initialized should still be True after pipe_2")
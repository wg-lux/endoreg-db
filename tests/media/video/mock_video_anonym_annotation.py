from endoreg_db.models import LabelVideoSegment
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.media.video.test_video_file_extracted import VideoFileModelExtractedTest

def mock_video_anonym_annotation(test:"VideoFileModelExtractedTest"):
    # This simulates validation, e.g., verifying sensitive meta
    video_file = test.video_file
    success = video_file.test_after_pipe_1()
    test.assertTrue(success, "test_after_pipe_1 failed: Simulating Post-Validation Processing failed.")

    # --- Assertions after test_after_pipe_1 ---
    video_file.refresh_from_db()
    test.assertIsNotNone(video_file.sensitive_meta, "SensitiveMeta should still exist after test_after_pipe_1")
    sensitive_meta_state = video_file.sensitive_meta.state
    test.assertIsNotNone(sensitive_meta_state, "SensitiveMetaState should exist after test_after_pipe_1")
    sensitive_meta_state.refresh_from_db()
    # Check if the specific function _test_after_pipe_1 sets these flags
    test.assertTrue(sensitive_meta_state.dob_verified, "SensitiveMetaState.dob_verified should be True")
    test.assertTrue(sensitive_meta_state.names_verified, "SensitiveMetaState.names_verified should be True")
    test.assertTrue(sensitive_meta_state.is_verified, "SensitiveMetaState.is_verified should be True")

    # Check Label Video Segments are still present
    lvs_exists = LabelVideoSegment.objects.filter(video_file=video_file).exists()
    test.assertTrue(lvs_exists, "LabelVideoSegments should still exist after test_after_pipe_1")

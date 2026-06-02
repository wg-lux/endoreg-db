from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile

    from .test_video_file_extracted import VideoFileModelExtractedTest


def _test_video_anonymization(test: "VideoFileModelExtractedTest") -> None:
    video_file = test.video_file

    is_mock_video = (
        hasattr(video_file, "__class__")
        and video_file.__class__.__name__ == "MockVideoFile"
    )

    success = video_file.anonymize(delete_original_raw=True)
    test.assertTrue(success, "Video anonymization failed.")

    if is_mock_video:
        test.assertTrue(
            hasattr(video_file, "is_processed"),
            "MockVideoFile should have is_processed attribute",
        )
        return

    video_file = cast("VideoFile", video_file)
    video_file.refresh_from_db()
    state = video_file.state
    test.assertIsNotNone(state, "VideoState should exist after anonymization")
    if state is None:
        raise AssertionError("VideoState should exist after anonymization")
    state.refresh_from_db()

    test.assertTrue(video_file.is_processed, "VideoFile should be marked as processed")
    test.assertIsNotNone(
        video_file.processed_file,
        "processed_file field should be set",
    )
    test.assertTrue(
        bool(video_file.processed_file.name),
        "processed_file field should have a name",
    )
    test.assertIsNotNone(
        video_file.processed_video_hash,
        "processed_video_hash should be set",
    )
    test.assertFalse(
        video_file.has_raw,
        "VideoFile should not have raw file after anonymization",
    )
    test.assertTrue(state.anonymized, "State.anonymized should be True")

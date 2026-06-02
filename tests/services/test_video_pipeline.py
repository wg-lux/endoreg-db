#!/usr/bin/env python3
"""
Corrected Video Import and Anonymization Test Script

This script demonstrates the complete pipeline for importing and processing a video:
1. Video import and metadata extraction
2. Temporal prediction segment materialization
3. User validation simulation
4. Video anonymization
"""

from pathlib import Path

from endoreg_db.models import VideoFile
from endoreg_db.services.video_import import VideoImportService
from endoreg_db.services.video_temporal_inference import _run_video_temporal_inference
from tests.helpers.default_objects import get_latest_segmentation_model
from tests.media.video.mock_video_anonym_annotation import mock_video_manual_validation

# Configuration
DEFAULT_ENDOSCOPY_PROCESSOR_NAME = "olympus_cv_1500"
DEFAULT_CENTER_NAME = "university_hospital_wuerzburg"
VIDEO_PATH = Path("/home/admin/dev/endoreg-db/tests/assets/test_outside.mp4")

vis = VideoImportService()
import_and_anonymize = vis.import_and_anonymize


def main():
    """Execute the complete video processing pipeline."""

    print("=== Video Import and Anonymization Pipeline ===")

    # Step 1: Import video and materialize prediction segments
    print(f"\n1. Importing video: {VIDEO_PATH}")

    if not VIDEO_PATH.exists():
        print(f"ERROR: Video file not found at {VIDEO_PATH}")
        return

    try:
        video_file = import_and_anonymize(
            file_path=VIDEO_PATH,
            center_name=DEFAULT_CENTER_NAME,
            processor_name=DEFAULT_ENDOSCOPY_PROCESSOR_NAME,
        )
        model_meta = get_latest_segmentation_model()
        _run_video_temporal_inference(
            video_file.pk,
            model_meta_id=model_meta.pk,
            delete_frames_after=False,
            frame_source_mode="stream",
        )

        print(f"✓ Video imported successfully with UUID: {video_file.video_hash}")
        assert isinstance(video_file, VideoFile)

        # Verify temporal prediction completed
        state = video_file.get_or_create_state()
        print(f"✓ Frames extracted: {state.frames_extracted}")
        print(f"✓ Text metadata extracted: {state.text_meta_extracted}")
        print(f"✓ Initial prediction completed: {state.initial_prediction_completed}")
        print(f"✓ Label video segments created: {state.lvs_created}")
        # Check sensitive metadata
        if video_file.sensitive_meta:
            print(f"✓ Sensitive metadata created: {video_file.sensitive_meta.id}")
        else:
            print("⚠ No sensitive metadata found")

    except Exception as e:
        print(f"✗ Error during import: {e}")
        return

    # Step 2: Simulate user validation
    print("\n2. Simulating user validation...")

    try:
        mock_video_manual_validation(video_file)
        print("✓ User validation simulation completed")

        # Verify validation state
        video_file.refresh_from_db()
        if video_file.sensitive_meta:
            sm_state = video_file.sensitive_meta.state
            if sm_state:
                print(f"✓ Sensitive meta verified: {sm_state.is_verified}")
                print(f"✓ DOB verified: {sm_state.dob_verified}")
                print(f"✓ Names verified: {sm_state.names_verified}")

    except Exception as e:
        print(f"✗ Error during validation: {e}")
        return

    # Step 3: Run video anonymization
    print("\n3. Starting video anonymization...")

    try:
        success = video_file.anonymize(delete_original_raw=True)

        if success:
            print("✓ Video anonymization completed")

            # Verify anonymization results
            video_file.refresh_from_db()
            print(f"✓ Video processed: {video_file.is_processed}")
            print(f"✓ Has processed file: {bool(video_file.processed_file)}")
            print(
                f"✓ Processed video hash: {video_file.processed_video_hash[:8] if video_file.processed_video_hash else 'None'}..."
            )

            # Check if raw file was deleted (should be)
            print(f"✓ Raw file deleted: {not video_file.has_raw}")

            # Check final state
            final_state = video_file.get_or_create_state()
            print(f"✓ Finally anonymized: {final_state.anonymized}")

        else:
            print("✗ Video anonymization failed")
            return

    except Exception as e:
        print(f"✗ Error during anonymization: {e}")
        return

    print("\n=== Pipeline Completed Successfully ===")
    print(f"Video UUID: {video_file.video_hash}")
    print(
        f"Processed file: {video_file.processed_file.name if video_file.processed_file else 'None'}"
    )


if __name__ == "__main__":
    main()

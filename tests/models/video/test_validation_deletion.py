"""
Test to verify that validation deletes RAW video, not processed (anonymized) video.

This test ensures the critical bug fix where validate_metadata_annotation()
was incorrectly deleting the processed video instead of the raw video.
"""

from datetime import date
from unittest.mock import Mock, patch

import pytest

from endoreg_db.models import Center, EndoscopyProcessor, VideoFile


@pytest.mark.django_db
class TestVideoValidationDeletionBehavior:
    """
    Test suite to verify correct file deletion during validation.

    **Expected Behavior:**
    - Validation should delete RAW video file
    - Validation should PRESERVE processed (anonymized) video file
    - After validation, only anonymized video remains
    """

    @pytest.fixture
    def center(self):
        """Create test center."""
        return Center.objects.create(
            name="test_center_validation", display_name="Test Center Validation"
        )

    @pytest.fixture
    def processor(self, center):
        """Create test processor."""
        processor = EndoscopyProcessor.objects.create(
            name="test_processor_validation",
            image_width=1920,
            image_height=1080,
            endoscope_image_x=0,
            endoscope_image_y=0,
            endoscope_image_width=1920,
            endoscope_image_height=1080,
            examination_date_x=0,
            examination_date_y=0,
            examination_date_width=100,
            examination_date_height=50,
            examination_time_x=0,
            examination_time_y=0,
            examination_time_width=100,
            examination_time_height=50,
            patient_first_name_x=0,
            patient_first_name_y=0,
            patient_first_name_width=100,
            patient_first_name_height=50,
            patient_last_name_x=0,
            patient_last_name_y=0,
            patient_last_name_width=100,
            patient_last_name_height=50,
            patient_dob_x=0,
            patient_dob_y=0,
            patient_dob_width=100,
            patient_dob_height=50,
            endoscope_type_x=0,
            endoscope_type_y=0,
            endoscope_type_width=100,
            endoscope_type_height=50,
            endoscope_sn_x=0,
            endoscope_sn_y=0,
            endoscope_sn_width=100,
            endoscope_sn_height=50,
        )
        processor.centers.add(center)
        return processor

    def test_validation_deletes_raw_video_only(self, center, processor, tmp_path):
        """
        Test that validation deletes RAW video but preserves PROCESSED video.

        **Test Scenario:**
        1. Create VideoFile with both raw and processed files
        2. Call validate_metadata_annotation()
        3. Verify RAW file is deleted
        4. Verify PROCESSED file is preserved
        """
        # Create mock video file paths
        raw_video_path = tmp_path / "raw_video.mp4"
        processed_video_path = tmp_path / "processed_video.mp4"

        # Create actual files
        raw_video_path.write_text("raw video content")
        processed_video_path.write_text("processed video content")

        # Create VideoFile instance
        video = VideoFile.objects.create(
            center=center, processor=processor, video_hash="test-hash-validation"
        )

        # Mock the file paths and _update_text_metadata
        with (
            patch(
                "endoreg_db.models.media.video.video_file_io._get_raw_file_path",
                return_value=raw_video_path,
            ),
            patch(
                "endoreg_db.models.media.video.video_file_io._get_processed_file_path",
                return_value=processed_video_path,
            ),
            patch(
                "endoreg_db.models.media.video.video_file._update_text_metadata"
            ) as mock_update,
        ):
            # Mock sensitive meta update
            mock_sm = Mock()
            mock_update.return_value = mock_sm

            # Verify both files exist before validation
            assert raw_video_path.exists(), "Raw video should exist before validation"
            assert processed_video_path.exists(), (
                "Processed video should exist before validation"
            )

            # Run validation
            validation_data = {
                "patient_first_name": "Max",
                "patient_last_name": "Mustermann",
                "patient_dob": date(1990, 1, 1),
            }
            result = video.validate_metadata_annotation(validation_data)

            # Assert validation succeeded
            assert result is True, "Validation should succeed"

            # CRITICAL ASSERTIONS: Verify correct file deletion
            assert not raw_video_path.exists(), (
                "❌ BUG: Raw video should be DELETED after validation"
            )

            assert processed_video_path.exists(), (
                "✅ CORRECT: Processed (anonymized) video should be PRESERVED after validation"
            )

    def test_validation_handles_missing_raw_video(self, center, processor):
        """
        Test that validation gracefully handles case where raw video doesn't exist.

        **Test Scenario:**
        1. Create VideoFile with only processed file (raw already deleted)
        2. Call validate_metadata_annotation()
        3. Verify validation succeeds without errors
        4. Verify processed file is still preserved
        """
        video = VideoFile.objects.create(
            center=center, processor=processor, video_hash="test-hash-no-raw"
        )

        # Mock: raw_file doesn't exist, only processed
        with (
            patch(
                "endoreg_db.models.media.video.video_file_io._get_raw_file_path",
                return_value=None,
            ),
            patch(
                "endoreg_db.models.media.video.video_file._update_text_metadata"
            ) as mock_update,
        ):
            mock_sm = Mock()
            mock_update.return_value = mock_sm

            # Run validation
            result = video.validate_metadata_annotation(
                {"patient_first_name": "Test", "patient_last_name": "User"}
            )

            # Should succeed even without raw file
            assert result is True, (
                "Validation should succeed even when raw file is missing"
            )

    def test_validation_with_only_raw_video(self, center, processor, tmp_path):
        """
        Test validation when only raw video exists (no processed yet).

        **Test Scenario:**
        1. Create VideoFile with only raw file
        2. Call validate_metadata_annotation()
        3. Verify raw file is deleted

        **Note:** This is an edge case - normally processed file should exist
        before validation is called.
        """
        raw_video_path = tmp_path / "only_raw.mp4"
        raw_video_path.write_text("raw content")

        video = VideoFile.objects.create(
            center=center, processor=processor, video_hash="test-hash-only-raw"
        )

        with (
            patch(
                "endoreg_db.models.media.video.video_file_io._get_raw_file_path",
                return_value=raw_video_path,
            ),
            patch(
                "endoreg_db.models.media.video.video_file_io._get_processed_file_path",
                return_value=None,
            ),
            patch(
                "endoreg_db.models.media.video.video_file._update_text_metadata"
            ) as mock_update,
        ):
            mock_sm = Mock()
            mock_update.return_value = mock_sm

            assert raw_video_path.exists(), "Raw video should exist before validation"

            # Run validation
            result = video.validate_metadata_annotation({})

            # Verify raw is deleted
            assert not raw_video_path.exists(), (
                "Raw video should be deleted even when it's the only file"
            )

            assert result is True, "Validation should succeed"


@pytest.mark.django_db
class TestActiveFileLogicWithValidation:
    """
    Test to ensure active_file logic doesn't interfere with validation deletion.

    This verifies that the fix correctly targets raw_file instead of active_file.
    """

    @pytest.fixture
    def center(self):
        return Center.objects.create(name="test_center_active")

    @pytest.fixture
    def processor(self, center):
        processor = EndoscopyProcessor.objects.create(
            name="test_processor_active",
            image_width=1920,
            image_height=1080,
            endoscope_image_x=0,
            endoscope_image_y=0,
            endoscope_image_width=1920,
            endoscope_image_height=1080,
            examination_date_x=100,
            examination_date_y=50,
            examination_date_width=200,
            examination_date_height=30,
            patient_first_name_x=100,
            patient_first_name_y=100,
            patient_first_name_width=200,
            patient_first_name_height=30,
            patient_last_name_x=100,
            patient_last_name_y=150,
            patient_last_name_width=200,
            patient_last_name_height=30,
            patient_dob_x=100,
            patient_dob_y=200,
            patient_dob_width=200,
            patient_dob_height=30,
        )
        processor.centers.add(center)
        return processor

    def test_active_file_returns_processed_when_both_exist(self, center, processor):
        """
        Verify that active_file returns processed file when both files exist.

        This confirms the original behavior that led to the bug.
        """
        from django.core.files.base import ContentFile

        video = VideoFile.objects.create(
            center=center, processor=processor, video_hash="test-active-file"
        )

        # Simulate both files existing
        video.raw_file.save("raw.mp4", ContentFile(b"raw"), save=False)
        video.processed_file.save(
            "processed.mp4", ContentFile(b"processed"), save=False
        )
        video.save()

        # active_file should return processed file
        active = video.active_file
        assert active is video.processed_file, (
            "active_file should return processed_file when both exist"
        )

    def test_validation_uses_raw_file_path_not_active(
        self, center, processor, tmp_path
    ):
        """
        Verify that validation explicitly uses raw_file_path, not active_file_path.

        This is the core of the bug fix.
        """
        raw_path = tmp_path / "raw.mp4"
        processed_path = tmp_path / "processed.mp4"

        raw_path.write_text("raw")
        processed_path.write_text("processed")

        video = VideoFile.objects.create(
            center=center, processor=processor, video_hash="test-explicit-raw"
        )

        with (
            patch(
                "endoreg_db.models.media.video.video_file_io._get_raw_file_path",
                return_value=raw_path,
            ),
            patch(
                "endoreg_db.models.media.video.video_file_io._get_processed_file_path",
                return_value=processed_path,
            ),
            patch(
                "endoreg_db.models.media.video.video_file._update_text_metadata"
            ) as mock_update,
        ):
            mock_update.return_value = Mock()

            # Before validation
            assert raw_path.exists()
            assert processed_path.exists()

            # Validate
            video.validate_metadata_annotation({})

            # After validation
            assert not raw_path.exists(), "Raw should be deleted"
            assert processed_path.exists(), "Processed should remain"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

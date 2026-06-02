from __future__ import annotations

from typing import TYPE_CHECKING

from endoreg_db.models import Label, LabelVideoSegment
from endoreg_db.models.media.video.video_file import VideoFile

if TYPE_CHECKING:
    from tests.media.video.test_video_file_extracted import VideoFileModelExtractedTest


def _assert_true(subject, value: bool, message: str) -> None:
    if hasattr(subject, "assertTrue"):
        subject.assertTrue(value, message)
        return
    if not value:
        raise AssertionError(message)


def _assert_is_not_none(subject, value, message: str) -> None:
    if hasattr(subject, "assertIsNotNone"):
        subject.assertIsNotNone(value, message)
        return
    if value is None:
        raise AssertionError(message)


def _resolve_video_file(subject):
    return getattr(subject, "video_file", subject)


def _simulate_manual_validation(video_file) -> bool:
    if not isinstance(video_file, VideoFile):
        if hasattr(video_file, "simulate_manual_validation"):
            return bool(video_file.simulate_manual_validation())
        return True

    outside_label = Label.objects.resolve_by_name("outside", case_insensitive=True)
    if outside_label is None:
        return False

    outside_segment = LabelVideoSegment.objects.create(
        video_file=video_file,
        label=outside_label,
        start_frame_number=0,
        end_frame_number=100,
        prediction_meta=None,
    )
    segment_state, _created = outside_segment.get_or_create_state()
    segment_state.is_validated = True
    segment_state.save()

    if video_file.sensitive_meta is None:
        from endoreg_db.import_files.context.default_sensitive_meta import (
            default_sensitive_meta,
        )

        sensitive_meta = default_sensitive_meta(video_file)
        if sensitive_meta is None:
            return False
        video_file.refresh_from_db()

    sensitive_meta = video_file.sensitive_meta
    if sensitive_meta is None:
        return False

    sensitive_state = sensitive_meta.get_or_create_state()
    sensitive_state.dob_verified = True
    sensitive_state.names_verified = True
    sensitive_state.save()

    return True


def mock_video_manual_validation(subject: VideoFileModelExtractedTest | VideoFile):
    video_file = _resolve_video_file(subject)
    success = _simulate_manual_validation(video_file)
    _assert_true(
        subject,
        success,
        "Manual validation simulation failed.",
    )

    video_file.refresh_from_db()
    sensitive_meta = video_file.sensitive_meta
    if sensitive_meta is not None:
        sensitive_meta_state = sensitive_meta.state
        _assert_is_not_none(
            subject,
            sensitive_meta_state,
            "SensitiveMetaState should exist after manual validation",
        )
        assert sensitive_meta_state is not None
        sensitive_meta_state.refresh_from_db()
        _assert_true(
            subject,
            sensitive_meta_state.dob_verified,
            "SensitiveMetaState.dob_verified should be True",
        )
        _assert_true(
            subject,
            sensitive_meta_state.names_verified,
            "SensitiveMetaState.names_verified should be True",
        )
        _assert_true(
            subject,
            sensitive_meta_state.is_verified,
            "SensitiveMetaState.is_verified should be True",
        )

    # Check Label Video Segments are still present - handle mock vs real objects
    if not isinstance(video_file, VideoFile):
        # For mock objects, we simulate that LabelVideoSegments exist
        lvs_exists = True  # Simulate that segments exist for mock testing
    else:
        # For real video files, do the actual database query
        lvs_exists = LabelVideoSegment.objects.filter(video_file=video_file).exists()

    _assert_true(
        subject,
        lvs_exists,
        "LabelVideoSegments should still exist after manual validation",
    )

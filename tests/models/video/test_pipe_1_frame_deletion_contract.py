import uuid

import pytest

from endoreg_db.models import Center, EndoscopyProcessor, Frame, VideoFile


@pytest.mark.django_db
def test_pipe_1_frame_deletion_keeps_db_frames_and_clears_extracted_flags(
    tmp_path,
):
    """
    Contract test for frame behavior in the pipeline deletion step.

    Expected end state:
    - Frame DB rows are preserved (count stays equal to initialized frame_count).
    - Extracted flags are cleared (all frames is_extracted=False).
    - VideoState reflects deletion side effect (frames_extracted=False) while
      keeping initialization metadata (frames_initialized=True, frame_count unchanged).

    This validates the same `video.delete_frames()` behavior used in `pipe_1`
    when `delete_frames_after=True`.
    """
    center = Center.objects.create(
        name=f"frame-contract-center-{uuid.uuid4().hex[:8]}",
        display_name="Frame Contract Center",
    )
    processor = EndoscopyProcessor.objects.create(
        name=f"frame-contract-processor-{uuid.uuid4().hex[:8]}",
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

    expected_final_frame_count = 12
    frame_dir = tmp_path / f"frames-{uuid.uuid4().hex[:8]}"
    frame_dir.mkdir(parents=True, exist_ok=True)
    video = VideoFile.objects.create(
        center=center,
        processor=processor,
        video_hash=f"pipe1-frame-contract-{uuid.uuid4().hex}",
        frame_count=expected_final_frame_count,
        frame_dir=str(frame_dir),
    )

    # Initialize frame rows as done in normal setup.
    video.initialize_frames()
    assert Frame.objects.filter(video=video).count() == expected_final_frame_count

    # Simulate pipeline pre-delete state: extracted files exist and flags are set.
    for frame in Frame.objects.filter(video=video):
        frame_path = frame_dir / frame.relative_path
        frame_path.write_bytes(b"dummy-frame-content")
    Frame.objects.filter(video=video).update(is_extracted=True)
    state = video.get_or_create_state()
    state.frames_extracted = True
    state.save(update_fields=["frames_extracted"])

    # Pipeline deletion step (same method called in pipe_1 finally block).
    video.delete_frames()

    total_frames_after_pipeline = Frame.objects.filter(video=video).count()
    extracted_frames_after_pipeline = Frame.objects.filter(
        video=video, is_extracted=True
    ).count()
    state.refresh_from_db()

    assert total_frames_after_pipeline == expected_final_frame_count, (
        "Pipeline should preserve initialized Frame DB rows after delete_frames; "
        f"expected {expected_final_frame_count}, got {total_frames_after_pipeline}."
    )
    assert extracted_frames_after_pipeline == 0, (
        "Pipeline cleanup should clear extracted frame flags; "
        f"got {extracted_frames_after_pipeline} extracted rows."
    )
    assert state.frames_extracted is False
    assert state.frames_initialized is True
    assert state.frame_count == expected_final_frame_count

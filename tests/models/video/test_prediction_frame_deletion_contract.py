from __future__ import annotations

# pyright: reportPrivateUsage=false

import uuid
from pathlib import Path
from typing import Any, Callable, Protocol, cast

import pytest
from pytest import MonkeyPatch

from endoreg_db.models import (
    AIDataSet,
    Center,
    EndoscopyProcessor,
    Frame,
    ImageClassificationAnnotation,
    Label,
    VideoFile,
)
from endoreg_db.services.video_files._io import _get_temp_anonymized_frame_dir


class _CenterRelation(Protocol):
    def add(self, *objs: Center | int) -> None: ...


def _add_center(processor: EndoscopyProcessor, center: Center) -> None:
    cast(_CenterRelation, processor.centers).add(center)


@pytest.mark.django_db
def test_prediction_frame_deletion_keeps_db_frames_and_clears_extracted_flags(
    tmp_path: Path,
) -> None:
    """
    Contract test for frame behavior in the pipeline deletion step.

    Expected end state:
    - Frame DB rows are preserved (count stays equal to initialized frame_count).
    - Extracted flags are cleared (all frames is_extracted=False).
    - VideoState reflects deletion side effect (frames_extracted=False) while
      keeping initialization metadata (frames_initialized=True, frame_count unchanged).

    This validates the `video.delete_frames()` cleanup behavior used by
    explicit cache consumers when `delete_frames_after=True`.
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
    _add_center(processor, center)

    expected_final_frame_count = 12
    frame_dir = tmp_path / f"frames-{uuid.uuid4().hex[:8]}"
    frame_dir.mkdir(parents=True, exist_ok=True)
    video = VideoFile.objects.create(
        center=center,
        processor=processor,
        video_hash=f"prediction-frame-contract-{uuid.uuid4().hex}",
        frame_count=expected_final_frame_count,
        frame_dir=str(frame_dir),
    )

    # Initialize frame rows as done in normal setup.
    video.initialize_frames()
    assert Frame.objects.filter(video=video).count() == expected_final_frame_count

    # Simulate pipeline pre-delete state: extracted files exist and flags are set.
    for frame in Frame.objects.filter(video=video):
        frame_path = frame_dir / str(frame.relative_path)
        frame_path.write_bytes(b"dummy-frame-content")
    Frame.objects.filter(video=video).update(is_extracted=True)
    state = video.get_or_create_state()
    state.frames_extracted = True
    state.save(update_fields=["frames_extracted"])

    # Cleanup step used after cache-backed derived frame processing.
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


@pytest.mark.django_db
def test_delete_frames_preserves_dataset_backed_frame_files(
    tmp_path: Path,
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    center = Center.objects.create(
        name=f"frame-preserve-center-{uuid.uuid4().hex[:8]}",
        display_name="Frame Preserve Center",
    )
    processor = EndoscopyProcessor.objects.create(
        name=f"frame-preserve-processor-{uuid.uuid4().hex[:8]}",
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
    _add_center(processor, center)
    frame_dir = tmp_path / f"frames-{uuid.uuid4().hex[:8]}"
    frame_dir.mkdir(parents=True, exist_ok=True)
    video = VideoFile.objects.create(
        center=center,
        processor=processor,
        video_hash=f"frame-preserve-{uuid.uuid4().hex}",
        frame_count=3,
        frame_dir=str(frame_dir),
    )
    video.initialize_frames()
    frames = list(Frame.objects.filter(video=video).order_by("frame_number"))
    for frame in frames:
        frame.file_path.write_bytes(f"frame-{frame.frame_number}".encode("utf-8"))
    Frame.objects.filter(video=video).update(is_extracted=True)

    label = Label.objects.create(name=f"frame-preserve-label-{uuid.uuid4().hex[:8]}")
    annotation = ImageClassificationAnnotation.objects.create(
        frame=frames[1],
        label=label,
        value=True,
    )
    dataset = AIDataSet.objects.create(
        name=f"frame-preserve-dataset-{uuid.uuid4().hex[:8]}",
        dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
        ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
    )
    dataset.image_annotations.add(annotation)

    state = video.get_or_create_state()
    state.frames_extracted = True
    state.save(update_fields=["frames_extracted"])

    with django_capture_on_commit_callbacks(execute=True):
        video.delete_frames()

    for frame in frames:
        frame.refresh_from_db()
    state.refresh_from_db()

    assert frames[0].file_path.exists() is False
    assert frames[1].file_path.read_bytes() == b"frame-1"
    assert frames[2].file_path.exists() is False
    assert frames[0].is_extracted is False
    assert frames[1].is_extracted is True
    assert frames[2].is_extracted is False
    assert state.frames_extracted is False


@pytest.mark.django_db
def test_delete_frames_restores_staged_directories_when_state_update_fails(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    center = Center.objects.create(
        name=f"frame-restore-center-{uuid.uuid4().hex[:8]}",
        display_name="Frame Restore Center",
    )
    processor = EndoscopyProcessor.objects.create(
        name=f"frame-restore-processor-{uuid.uuid4().hex[:8]}",
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
    _add_center(processor, center)

    frame_dir = tmp_path / f"frames-{uuid.uuid4().hex[:8]}"
    frame_dir.mkdir(parents=True, exist_ok=True)
    video = VideoFile.objects.create(
        center=center,
        processor=processor,
        video_hash=f"pipe1-frame-restore-{uuid.uuid4().hex}",
        frame_count=1,
        frame_dir=str(frame_dir),
    )

    video.initialize_frames()
    frame = Frame.objects.get(video=video)
    frame_path = frame_dir / str(frame.relative_path)
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    frame_path.write_bytes(b"dummy-frame-content")

    temp_anonym_dir = _get_temp_anonymized_frame_dir(video)
    temp_anonym_dir.mkdir(parents=True, exist_ok=True)
    temp_frame_path = temp_anonym_dir / "temp.jpg"
    temp_frame_path.write_bytes(b"temp-frame-content")

    Frame.objects.filter(video=video).update(is_extracted=True)
    state = video.get_or_create_state()
    state.frames_extracted = True
    state.save(update_fields=["frames_extracted"])

    original_state_save = type(state).save

    def failing_save(
        self: object,
        force_insert: bool = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: list[str] | None = None,
    ) -> object:
        if (
            getattr(self, "pk", None) == state.pk
            and update_fields == ["frames_extracted"]
        ):
            raise RuntimeError("simulated state persistence failure")
        return original_state_save(
            cast(Any, self),
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    monkeypatch.setattr(type(state), "save", failing_save)

    with pytest.raises(
        RuntimeError,
        match="Failed to update state during frame file deletion",
    ):
        video.delete_frames()

    state.refresh_from_db()
    frame.refresh_from_db()

    assert frame_dir.exists()
    assert frame_path.exists()
    assert temp_anonym_dir.exists()
    assert temp_frame_path.exists()
    assert state.frames_extracted is True
    assert frame.is_extracted is True

    pending_delete_paths: list[Path] = list(
        frame_dir.parent.glob("*.pending_delete.*")
    )
    pending_delete_paths.extend(
        temp_anonym_dir.parent.glob("*.pending_delete.*")
    )
    assert list(pending_delete_paths) == []

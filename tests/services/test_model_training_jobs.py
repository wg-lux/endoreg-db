from __future__ import annotations

import uuid

import pytest

from endoreg_db.models import (
    AIDataSet,
    Center,
    Frame,
    ImageClassificationAnnotation,
    Label,
    VideoFile,
)
from endoreg_db.services import model_training_jobs


@pytest.mark.django_db
def test_prepare_model_training_inputs_materializes_missing_frames_from_processed_video(
    tmp_path,
    monkeypatch,
):
    center = Center.objects.create(
        name=f"training-center-{uuid.uuid4().hex[:8]}",
        display_name="Training Center",
    )
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"training-video-{uuid.uuid4().hex}",
        frame_dir=str(tmp_path / "frames"),
        processed_file="anonymized_videos/processed.mp4",
    )
    state = video.get_or_create_state()
    state.sensitive_meta_processed = True
    state.anonymized = True
    state.anonymization_validated = True
    state.outside_segments_removed = True
    state.save(
        update_fields=[
            "sensitive_meta_processed",
            "anonymized",
            "anonymization_validated",
            "outside_segments_removed",
        ]
    )
    frame = Frame.objects.create(
        video=video,
        frame_number=7,
        relative_path="frame_0000007.jpg",
        is_extracted=False,
    )
    label = Label.objects.create(name=f"training-label-{uuid.uuid4().hex[:8]}")
    annotation = ImageClassificationAnnotation.objects.create(
        frame=frame,
        label=label,
        value=True,
    )
    dataset = AIDataSet.objects.create(
        name=f"training-dataset-{uuid.uuid4().hex[:8]}",
        dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
        ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
    )
    dataset.image_annotations.add(annotation)

    calls: list[dict[str, object]] = []

    def fake_extract_frame_range_to_directory(video_arg, **kwargs):
        calls.append(kwargs)
        calls[-1]["video"] = video_arg
        output_dir = kwargs["output_dir"]
        (output_dir / "frame_0000007.jpg").write_bytes(b"frame")
        return [output_dir / "frame_0000007.jpg"]

    monkeypatch.setattr(
        model_training_jobs,
        "extract_frame_range_to_directory",
        fake_extract_frame_range_to_directory,
    )

    result = model_training_jobs.prepare_model_training_inputs(
        {"dataset_id": dataset.pk}
    )

    frame.refresh_from_db()
    assert result["prepared"] is True
    assert result["materialized_frame_count"] == 1
    assert frame.is_extracted is True
    assert frame.file_path.read_bytes() == b"frame"
    assert calls[0]["from_processed"] is True
    assert calls[0]["start_frame"] == 7
    assert calls[0]["end_frame"] == 8


@pytest.mark.django_db
def test_prepare_model_training_inputs_rejects_unready_processed_video(
    tmp_path,
    monkeypatch,
):
    center = Center.objects.create(
        name=f"training-unready-center-{uuid.uuid4().hex[:8]}",
        display_name="Training Unready Center",
    )
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"training-unready-video-{uuid.uuid4().hex}",
        frame_dir=str(tmp_path / "frames"),
        processed_file="anonymized_videos/processed.mp4",
    )
    frame = Frame.objects.create(
        video=video,
        frame_number=3,
        relative_path="frame_0000003.jpg",
        is_extracted=False,
    )
    label = Label.objects.create(name=f"training-unready-label-{uuid.uuid4().hex[:8]}")
    annotation = ImageClassificationAnnotation.objects.create(
        frame=frame,
        label=label,
        value=True,
    )
    dataset = AIDataSet.objects.create(
        name=f"training-unready-dataset-{uuid.uuid4().hex[:8]}",
        dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
        ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
    )
    dataset.image_annotations.add(annotation)

    def fail_extract_frame_range_to_directory(**kwargs):
        raise AssertionError("unready videos must not be extracted")

    monkeypatch.setattr(
        model_training_jobs,
        "extract_frame_range_to_directory",
        fail_extract_frame_range_to_directory,
    )

    with pytest.raises(RuntimeError, match="missing readiness flags"):
        model_training_jobs.prepare_model_training_inputs({"dataset_id": dataset.pk})

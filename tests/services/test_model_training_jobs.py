from __future__ import annotations

import uuid

import pytest

from endoreg_db.models import (
    AIDataSet,
    Center,
    Frame,
    ImageClassificationAnnotation,
    Label,
    LabelVideoSegment,
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
    assert result["annotation_source_scope"] == "all"
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


@pytest.mark.django_db
def test_prepare_model_training_inputs_materializes_dataset_video_annotation_frames(
    tmp_path,
    monkeypatch,
):
    center = Center.objects.create(
        name=f"training-segment-center-{uuid.uuid4().hex[:8]}",
        display_name="Training Segment Center",
    )
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"training-segment-video-{uuid.uuid4().hex}",
        frame_dir=str(tmp_path / "frames"),
        processed_file="anonymized_videos/processed.mp4",
    )
    frame = Frame.objects.create(
        video=video,
        frame_number=7,
        relative_path="frame_0000007.jpg",
        is_extracted=False,
    )
    label = Label.objects.create(name=f"training-segment-label-{uuid.uuid4().hex[:8]}")
    segment = LabelVideoSegment.objects.create(
        video_file=video,
        label=label,
        start_frame_number=7,
        end_frame_number=8,
    )
    dataset = AIDataSet.objects.create(
        name=f"training-segment-dataset-{uuid.uuid4().hex[:8]}",
        dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
        ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
    )
    dataset.video_annotations.add(segment)

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
    assert result["annotation_source_scope"] == "all"
    assert result["materialized_frame_count"] == 1
    assert frame.is_extracted is True
    assert frame.file_path.read_bytes() == b"frame"
    assert calls[0]["from_processed"] is True
    assert calls[0]["start_frame"] == 7
    assert calls[0]["end_frame"] == 8


@pytest.mark.django_db
def test_prepare_model_training_inputs_skips_segments_for_frame_only_scope(
    tmp_path,
    monkeypatch,
):
    center = Center.objects.create(
        name=f"training-frame-only-center-{uuid.uuid4().hex[:8]}",
        display_name="Training Frame Only Center",
    )
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"training-frame-only-video-{uuid.uuid4().hex}",
        frame_dir=str(tmp_path / "frames"),
        processed_file="anonymized_videos/processed.mp4",
    )
    frame = Frame.objects.create(
        video=video,
        frame_number=7,
        relative_path="frame_0000007.jpg",
        is_extracted=False,
    )
    label = Label.objects.create(
        name=f"training-frame-only-label-{uuid.uuid4().hex[:8]}"
    )
    segment = LabelVideoSegment.objects.create(
        video_file=video,
        label=label,
        start_frame_number=7,
        end_frame_number=8,
    )
    dataset = AIDataSet.objects.create(
        name=f"training-frame-only-dataset-{uuid.uuid4().hex[:8]}",
        dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
        ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
    )
    dataset.video_annotations.add(segment)

    def fail_extract_frame_range_to_directory(*args, **kwargs):
        raise AssertionError("frame_only scope must not materialize segment frames")

    monkeypatch.setattr(
        model_training_jobs,
        "extract_frame_range_to_directory",
        fail_extract_frame_range_to_directory,
    )

    result = model_training_jobs.prepare_model_training_inputs(
        {"dataset_id": dataset.pk, "annotation_source_scope": "frame_only"}
    )

    frame.refresh_from_db()
    assert result["prepared"] is True
    assert result["annotation_source_scope"] == "frame_only"
    assert result["materialized_frame_count"] == 0
    assert frame.is_extracted is False


@pytest.mark.django_db
def test_prepare_model_training_inputs_skips_frame_annotations_for_segment_only_scope(
    tmp_path,
    monkeypatch,
):
    center = Center.objects.create(
        name=f"training-segment-only-center-{uuid.uuid4().hex[:8]}",
        display_name="Training Segment Only Center",
    )
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"training-segment-only-video-{uuid.uuid4().hex}",
        frame_dir=str(tmp_path / "frames"),
        processed_file="anonymized_videos/processed.mp4",
    )
    frame = Frame.objects.create(
        video=video,
        frame_number=11,
        relative_path="frame_0000011.jpg",
        is_extracted=False,
    )
    label = Label.objects.create(
        name=f"training-segment-only-label-{uuid.uuid4().hex[:8]}"
    )
    annotation = ImageClassificationAnnotation.objects.create(
        frame=frame,
        label=label,
        value=True,
    )
    dataset = AIDataSet.objects.create(
        name=f"training-segment-only-dataset-{uuid.uuid4().hex[:8]}",
        dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
        ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
    )
    dataset.image_annotations.add(annotation)

    def fail_extract_frame_range_to_directory(*args, **kwargs):
        raise AssertionError(
            "segment_only scope must not materialize frame annotations"
        )

    monkeypatch.setattr(
        model_training_jobs,
        "extract_frame_range_to_directory",
        fail_extract_frame_range_to_directory,
    )

    result = model_training_jobs.prepare_model_training_inputs(
        {"dataset_id": dataset.pk, "annotation_source_scope": "segment_only"}
    )

    frame.refresh_from_db()
    assert result["prepared"] is True
    assert result["annotation_source_scope"] == "segment_only"
    assert result["materialized_frame_count"] == 0
    assert frame.is_extracted is False


@pytest.mark.django_db
def test_prepare_model_training_inputs_only_materializes_sparse_segment_frames(
    tmp_path,
    monkeypatch,
):
    center = Center.objects.create(
        name=f"training-sparse-segment-center-{uuid.uuid4().hex[:8]}",
        display_name="Training Sparse Segment Center",
    )
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"training-sparse-segment-video-{uuid.uuid4().hex}",
        frame_dir=str(tmp_path / "frames"),
        processed_file="anonymized_videos/processed.mp4",
    )
    label = Label.objects.create(
        name=f"training-sparse-segment-label-{uuid.uuid4().hex[:8]}"
    )
    dataset = AIDataSet.objects.create(
        name=f"training-sparse-segment-dataset-{uuid.uuid4().hex[:8]}",
        dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
        ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
    )

    segment_frames: list[Frame] = []
    for index in range(121):
        frame_number = index * 2
        segment_frames.append(
            Frame.objects.create(
                video=video,
                frame_number=frame_number,
                relative_path=f"frame_{frame_number:07d}.jpg",
                is_extracted=False,
            )
        )
        segment = LabelVideoSegment.objects.create(
            video_file=video,
            label=label,
            start_frame_number=frame_number,
            end_frame_number=frame_number + 1,
        )
        dataset.video_annotations.add(segment)

    non_segment_frame = Frame.objects.create(
        video=video,
        frame_number=1,
        relative_path="frame_0000001.jpg",
        is_extracted=False,
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

    calls: list[dict[str, object]] = []

    def fake_extract_frame_range_to_directory(video_arg, **kwargs):
        calls.append({**kwargs, "video": video_arg})
        output_dir = kwargs["output_dir"]
        for frame_number in range(kwargs["start_frame"], kwargs["end_frame"]):
            (output_dir / f"frame_{frame_number:07d}.jpg").write_bytes(b"frame")
        return []

    monkeypatch.setattr(
        model_training_jobs,
        "extract_frame_range_to_directory",
        fake_extract_frame_range_to_directory,
    )

    result = model_training_jobs.prepare_model_training_inputs(
        {"dataset_id": dataset.pk, "annotation_source_scope": "segment_only"}
    )

    non_segment_frame.refresh_from_db()
    assert result["prepared"] is True
    assert result["annotation_source_scope"] == "segment_only"
    assert result["materialized_frame_count"] == len(segment_frames)
    assert non_segment_frame.is_extracted is False
    assert not non_segment_frame.file_path.exists()
    assert all(call["from_processed"] is True for call in calls)

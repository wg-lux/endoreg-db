# pyright: reportMissingTypeStubs=false
from __future__ import annotations

import uuid
from pathlib import Path
from typing import cast

import pytest
from django.test import override_settings
from pytest import MonkeyPatch

from endoreg_db.models import (
    AIDataSet,
    AIModelTrainingRun,
    Center,
    Frame,
    ImageClassificationAnnotation,
    Label,
    LabelVideoSegment,
    VideoFile,
)
from endoreg_db.services.jobs import model_training_jobs


def _phi_training_command_kwargs(
    dataset_yaml: Path, output_dir: Path
) -> dict[str, object]:
    return {
        "_command_name": "train_phi_region_detector",
        "dataset_yaml": str(dataset_yaml),
        "output_dir": str(output_dir),
        "base_model": "yolov8n.pt",
        "run_name": "worker-aaa",
        "epochs": 1,
        "batch_size": 2,
        "input_size": 512,
        "device": "cpu",
        "workers": 0,
        "patience": 1,
        "export_onnx": True,
        "confidence_threshold": 0.4,
        "nms_threshold": 0.5,
        "class_ids": "0",
    }


def _phi_training_run(command_kwargs: dict[str, object]) -> AIModelTrainingRun:
    return AIModelTrainingRun.objects.create(
        dataset=None,
        dataset_name="dataset.yml",
        dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
        ai_model_type=model_training_jobs.MODEL_TRAINING_TARGET_PHI_REGION_DETECTOR,
        backbone_name="yolov8n.pt",
        feature_mode="yolo_onnx_detector",
        freeze_backbone=False,
        epochs=1,
        batch_size=2,
        labelset_version=1,
        treat_unlabeled_as_negative=False,
        request_payload={"training_target": "phi_region_detector"},
        command_kwargs=command_kwargs,
        status=AIModelTrainingRun.STATUS_QUEUED,
        server_instance_id=model_training_jobs.MODEL_TRAINING_SERVER_INSTANCE_ID,
    )


@pytest.mark.django_db
def test_phi_retraining_worker_integrates_with_lx_anonymizer_and_persists_result(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    # Arrange
    from lx_anonymizer.text_detection import phi_region_detector_training

    dataset_yaml = tmp_path / "dataset.yml"
    dataset_yaml.write_text(
        "path: .\ntrain: images/train\nval: images/val\n", encoding="utf-8"
    )
    output_dir = tmp_path / "runs"
    command_kwargs = _phi_training_command_kwargs(dataset_yaml, output_dir)
    run = _phi_training_run(command_kwargs)
    captured_configs: list[
        phi_region_detector_training.PhiRegionDetectorTrainingConfig
    ] = []

    def fake_train_phi_region_detector(
        config: phi_region_detector_training.PhiRegionDetectorTrainingConfig,
    ) -> dict[str, object]:
        captured_configs.append(config)
        return {
            "model_path": str(output_dir / "phi.onnx"),
            "checkpoint_path": str(output_dir / "best.pt"),
            "onnx_path": str(output_dir / "phi.onnx"),
            "meta_path": str(output_dir / "phi.json"),
        }

    monkeypatch.setattr(
        phi_region_detector_training,
        "train_phi_region_detector",
        fake_train_phi_region_detector,
    )

    # Act
    with override_settings(MODEL_TRAINING_STAGING_ROOT=tmp_path / "staging"):
        model_training_jobs._execute_model_training_run(
            run.run_key,
            command_kwargs=command_kwargs,
        )

    # Assert
    run.refresh_from_db()
    assert len(captured_configs) == 1
    assert captured_configs[0].dataset_yaml == dataset_yaml.resolve()
    assert captured_configs[0].run_name == "worker-aaa"
    assert run.status == AIModelTrainingRun.STATUS_COMPLETED
    assert run.error == ""
    assert run.result["model_path"] == str(output_dir / "phi.onnx")
    assert run.artifact_paths == {
        "model_path": str(output_dir / "phi.onnx"),
        "checkpoint_path": str(output_dir / "best.pt"),
        "onnx_path": str(output_dir / "phi.onnx"),
        "meta_path": str(output_dir / "phi.json"),
    }
    assert '"reason": "not_image_multilabel"' in run.stdout


@pytest.mark.django_db
def test_phi_retraining_worker_marks_lx_anonymizer_failure_failed(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    # Arrange
    from lx_anonymizer.text_detection import phi_region_detector_training

    dataset_yaml = tmp_path / "dataset.yml"
    dataset_yaml.write_text(
        "path: .\ntrain: images/train\nval: images/val\n", encoding="utf-8"
    )
    command_kwargs = _phi_training_command_kwargs(dataset_yaml, tmp_path / "runs")
    run = _phi_training_run(command_kwargs)

    def fail_training(
        config: phi_region_detector_training.PhiRegionDetectorTrainingConfig,
    ) -> dict[str, object]:
        raise RuntimeError("lx-anonymizer training failed")

    monkeypatch.setattr(
        phi_region_detector_training,
        "train_phi_region_detector",
        fail_training,
    )

    # Act
    with override_settings(MODEL_TRAINING_STAGING_ROOT=tmp_path / "staging"):
        model_training_jobs._execute_model_training_run(
            run.run_key,
            command_kwargs=command_kwargs,
        )

    # Assert
    run.refresh_from_db()
    assert run.status == AIModelTrainingRun.STATUS_FAILED
    assert run.error == "lx-anonymizer training failed"
    assert "RuntimeError: lx-anonymizer training failed" in run.stdout
    assert run.result is None
    assert run.artifact_paths == {}


@pytest.mark.django_db
def test_prepare_model_training_inputs_materializes_missing_frames_from_processed_video(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
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

    def fake_extract_frame_range_to_directory(
        video_arg: VideoFile, **kwargs: object
    ) -> list[Path]:
        calls.append(dict(kwargs))
        calls[-1]["video"] = video_arg
        output_dir = cast(Path, kwargs["output_dir"])
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
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
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

    def fail_extract_frame_range_to_directory(**kwargs: object) -> None:
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
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
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

    def fake_extract_frame_range_to_directory(
        video_arg: VideoFile, **kwargs: object
    ) -> list[Path]:
        calls.append(dict(kwargs))
        calls[-1]["video"] = video_arg
        output_dir = cast(Path, kwargs["output_dir"])
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
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
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

    def fail_extract_frame_range_to_directory(*args: object, **kwargs: object) -> None:
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
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
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

    def fail_extract_frame_range_to_directory(*args: object, **kwargs: object) -> None:
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
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
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

    def fake_extract_frame_range_to_directory(
        video_arg: VideoFile, **kwargs: object
    ) -> list[Path]:
        calls.append({**kwargs, "video": video_arg})
        output_dir = cast(Path, kwargs["output_dir"])
        start_frame = cast(int, kwargs["start_frame"])
        end_frame = cast(int, kwargs["end_frame"])
        for frame_number in range(start_frame, end_frame):
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

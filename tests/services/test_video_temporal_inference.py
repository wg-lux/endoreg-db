from __future__ import annotations

import types
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest

from endoreg_db.models import (
    AiModel,
    Center,
    InformationSource,
    Label,
    LabelSet,
    LabelVideoSegment,
    ModelMeta,
    VideoFile,
    VideoProcessingHistory,
)
from endoreg_db.models.media.video.video_file_ai import VideoFrameScoreResult
from endoreg_db.services import video_temporal_inference as jobs


def _create_model_meta() -> tuple[ModelMeta, Label, Label]:
    label_a = Label.objects.create(name=f"temporal-a-{uuid.uuid4().hex[:8]}")
    label_b = Label.objects.create(name=f"temporal-b-{uuid.uuid4().hex[:8]}")
    label_set = LabelSet.objects.create(
        name=f"temporal-labels-{uuid.uuid4().hex[:8]}",
        version=1,
    )
    label_set.labels.add(label_a, label_b)
    ai_model = AiModel.objects.create(name=f"temporal-model-{uuid.uuid4().hex[:8]}")
    model_meta = ModelMeta.objects.create(
        name=f"temporal-meta-{uuid.uuid4().hex[:8]}",
        version="1",
        model=ai_model,
        labelset=label_set,
    )
    return model_meta, label_a, label_b


def _create_video(tmp_path: Path) -> VideoFile:
    center = Center.objects.create(name=f"temporal-center-{uuid.uuid4().hex[:8]}")
    frame_dir = tmp_path / f"frames-{uuid.uuid4().hex[:8]}"
    frame_dir.mkdir(parents=True)
    (frame_dir / "frame_0000000.jpg").write_bytes(b"not-real-image")
    return VideoFile.objects.create(
        center=center,
        video_hash=f"temporal-video-{uuid.uuid4().hex}",
        frame_count=4,
        frame_dir=str(frame_dir),
        fps=25.0,
    )


def test_build_lx_temporal_options_defaults_convert_seconds_to_frames():
    lx_options, history_options = jobs.build_lx_temporal_options({}, fps=25.0)

    assert lx_options["temporal_model"] == "hysteresis"
    assert lx_options["threshold"] == 0.5
    assert lx_options["min_length"] == 25
    assert lx_options["max_gap"] == 0
    assert lx_options["smoothing_window"] == 25
    assert lx_options["include_score_vectors"] is False
    assert history_options["fps"] == 25.0


def test_build_lx_temporal_options_accepts_label_keyed_thresholds():
    lx_options, _ = jobs.build_lx_temporal_options(
        {
            "threshold": {"polyp": 0.7, "outside": 0.4},
            "low_threshold": {"polyp": 0.55},
            "min_length_seconds": 0.01,
        },
        fps=25.0,
    )

    assert lx_options["threshold"] == 0.5
    assert lx_options["thresholds"] == {"polyp": 0.7, "outside": 0.4}
    assert lx_options["low_thresholds"] == {"polyp": 0.55}
    assert lx_options["min_length"] == 2


def test_build_lx_temporal_options_rejects_invalid_model():
    with pytest.raises(jobs.TemporalInferenceConfigError):
        jobs.build_lx_temporal_options({"temporal_model": "unknown"}, fps=25.0)


@pytest.mark.django_db
def test_dispatch_video_temporal_inference_uses_inference_queue(monkeypatch, tmp_path):
    video = _create_video(tmp_path)
    model_meta, _label_a, _label_b = _create_model_meta()
    monkeypatch.setenv("VIDEO_TEMPORAL_INFERENCE_JOB_MODE", "celery")
    monkeypatch.setenv("CELERY_INFERENCE_QUEUE", "inference_hi")
    fake_async_result = types.SimpleNamespace(id="temporal-task-1")
    fake_task = types.SimpleNamespace(apply_async=Mock(return_value=fake_async_result))
    monkeypatch.setattr(
        "endoreg_db.tasks.run_video_temporal_inference_task",
        fake_task,
        raising=False,
    )

    result = jobs.dispatch_video_temporal_inference(
        video_id=video.pk,
        model_meta_id=model_meta.pk,
    )

    assert result.status == "queued"
    assert result.queue == "inference_hi"
    assert result.task_id == "temporal-task-1"
    fake_task.apply_async.assert_called_once()
    assert fake_task.apply_async.call_args.kwargs["queue"] == "inference_hi"
    history = VideoProcessingHistory.objects.get(pk=result.history_id)
    assert history.operation == VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE
    assert history.config["kind"] == jobs.TEMPORAL_INFERENCE_KIND


@pytest.mark.django_db
def test_dispatch_video_temporal_inference_reuses_active_history(monkeypatch, tmp_path):
    video = _create_video(tmp_path)
    model_meta, _label_a, _label_b = _create_model_meta()
    submitted = []
    monkeypatch.setenv("VIDEO_TEMPORAL_INFERENCE_JOB_MODE", "thread")
    monkeypatch.setattr(
        jobs._executor,
        "submit",
        lambda fn: submitted.append(fn) or types.SimpleNamespace(),
    )

    first = jobs.dispatch_video_temporal_inference(
        video_id=video.pk,
        model_meta_id=model_meta.pk,
    )
    second = jobs.dispatch_video_temporal_inference(
        video_id=video.pk,
        model_meta_id=model_meta.pk,
    )

    assert first.status == "queued"
    assert second.status == "already_queued"
    assert second.history_id == first.history_id
    assert len(submitted) == 1


@pytest.mark.django_db
def test_dispatch_video_temporal_inference_busy_for_reprocessing(monkeypatch, tmp_path):
    video = _create_video(tmp_path)
    model_meta, _label_a, _label_b = _create_model_meta()
    monkeypatch.setenv("VIDEO_TEMPORAL_INFERENCE_JOB_MODE", "thread")
    VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        config={"kind": "outside_frame_blackening"},
    )

    result = jobs.dispatch_video_temporal_inference(
        video_id=video.pk,
        model_meta_id=model_meta.pk,
    )

    assert result.status == "busy"


@pytest.mark.django_db
def test_dispatch_video_temporal_inference_celery_failure_does_not_fallback(
    monkeypatch,
    tmp_path,
):
    video = _create_video(tmp_path)
    model_meta, _label_a, _label_b = _create_model_meta()
    monkeypatch.setenv("VIDEO_TEMPORAL_INFERENCE_JOB_MODE", "celery")
    monkeypatch.setattr(
        "endoreg_db.tasks.run_video_temporal_inference_task",
        types.SimpleNamespace(
            apply_async=Mock(side_effect=RuntimeError("broker down"))
        ),
        raising=False,
    )
    monkeypatch.setattr(
        jobs._executor,
        "submit",
        Mock(side_effect=AssertionError("must not fallback to thread")),
    )

    result = jobs.dispatch_video_temporal_inference(
        video_id=video.pk,
        model_meta_id=model_meta.pk,
    )

    assert result.status == "failed"
    history = VideoProcessingHistory.objects.get(pk=result.history_id)
    assert history.status == VideoProcessingHistory.STATUS_FAILURE
    assert "broker down" in history.details


@pytest.mark.django_db
def test_run_video_temporal_inference_materializes_lx_core_segments(
    monkeypatch,
    tmp_path,
):
    video = _create_video(tmp_path)
    model_meta, label_a, label_b = _create_model_meta()
    prediction_source = InformationSource.objects.create(name="prediction")
    old_segment = LabelVideoSegment.objects.create(
        video_file=video,
        label=label_b,
        start_frame_number=0,
        end_frame_number=2,
        source=prediction_source,
    )
    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        status=VideoProcessingHistory.STATUS_PENDING,
        config={"kind": jobs.TEMPORAL_INFERENCE_KIND},
    )

    monkeypatch.setattr(VideoFile, "update_video_meta", lambda self: None)
    monkeypatch.setattr(VideoFile, "extract_frames", lambda self, overwrite=False: True)
    monkeypatch.setattr(
        VideoFile,
        "update_text_metadata",
        lambda self, **kwargs: True,
    )
    monkeypatch.setattr(jobs, "_has_extracted_frame_files", lambda video_obj: True)

    def _fake_predict_video(self, **kwargs):
        assert kwargs["return_frame_scores"] is True
        return VideoFrameScoreResult(
            labels=[label_a.name, label_b.name],
            frame_scores=[
                [0.1, 0.8],
                [0.2, 0.9],
                [0.9, 0.1],
                [0.95, 0.2],
            ],
            device="cpu",
            frame_count=4,
        )

    monkeypatch.setattr(VideoFile, "predict_video", _fake_predict_video)

    def _fake_lx_core(**kwargs):
        assert kwargs["lx_options"]["include_score_vectors"] is False
        return types.SimpleNamespace(
            temporal_segments=[
                types.SimpleNamespace(label=label_a.name, start_frame=2, end_frame=3),
                types.SimpleNamespace(label=label_b.name, start_frame=0, end_frame=1),
            ],
            backend="torch",
            device="cpu",
            duration_ms=1.5,
            provenance={"local_only": True},
        )

    monkeypatch.setattr(jobs, "_run_lx_ai_core_temporal_inference", _fake_lx_core)

    assert jobs._run_video_temporal_inference(
        video.pk,
        model_meta_id=model_meta.pk,
        history_id=history.pk,
        delete_frames_after=False,
    )

    assert not LabelVideoSegment.objects.filter(pk=old_segment.pk).exists()
    assert (
        LabelVideoSegment.objects.filter(
            video_file=video,
            prediction_meta__model_meta=model_meta,
        ).count()
        == 2
    )
    video.refresh_from_db()
    state = video.get_or_create_state()
    assert state.initial_prediction_completed is True
    assert state.lvs_created is True
    history.refresh_from_db()
    assert history.status == VideoProcessingHistory.STATUS_SUCCESS
    assert history.config["result"]["score_vectors_stored"] is False
    assert history.config["result"]["deleted_prediction_segments"] == 1

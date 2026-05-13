from __future__ import annotations

import types
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from django.utils import timezone

from endoreg_db.models import (
    AiModel,
    Center,
    InformationSource,
    Label,
    LabelSet,
    LabelVideoSegment,
    ModelMeta,
    Frame,
    VideoFile,
    VideoPredictionMeta,
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


def test_coerce_lx_temporal_inference_result_rejects_missing_segments():
    with pytest.raises(RuntimeError, match="returned no segment list"):
        jobs._coerce_lx_temporal_inference_result(
            types.SimpleNamespace(
                backend="torch",
                device="cpu",
                duration_ms=1.0,
                provenance={},
            )
        )


def test_coerce_lx_temporal_inference_result_rejects_malformed_segment():
    with pytest.raises(RuntimeError, match="missing 'end_frame'"):
        jobs._coerce_lx_temporal_inference_result(
            types.SimpleNamespace(
                temporal_segments=[
                    types.SimpleNamespace(
                        label="polyp",
                        start_frame=0,
                    )
                ],
                backend="torch",
                device="cpu",
                duration_ms=1.0,
                provenance={},
            )
        )


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
    assert history.config["frame_source_mode"] == "stream"


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


@pytest.mark.django_db(transaction=True)
def test_dispatch_video_temporal_inference_expires_stale_running_history_and_rolls_back_frames(
    monkeypatch,
    tmp_path,
):
    video = _create_video(tmp_path)
    model_meta, _label_a, _label_b = _create_model_meta()
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    (frame_dir / "frame_0000000.jpg").write_bytes(b"partial")
    Frame.objects.create(
        video=video,
        frame_number=0,
        relative_path="frame_0000000.jpg",
        is_extracted=True,
    )
    state = video.get_or_create_state()
    state.frames_initialized = True
    state.frame_count = 1
    state.frames_extracted = True
    state.save(
        update_fields=[
            "frames_initialized",
            "frame_count",
            "frames_extracted",
        ]
    )
    monkeypatch.setenv("VIDEO_TEMPORAL_INFERENCE_JOB_MODE", "thread")
    stale_history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        status=VideoProcessingHistory.STATUS_RUNNING,
        task_id="stale-temporal-task",
        config=jobs._temporal_history_config(
            model_meta_id=model_meta.pk,
            replace_prediction_segments=True,
            delete_frames_after=True,
            ocr_frame_fraction=0.001,
            ocr_cap=10,
            temporal_options={},
            queue="inference",
            frame_source_mode="cache",
        ),
    )
    VideoProcessingHistory.objects.filter(pk=stale_history.pk).update(
        created_at=timezone.now()
        - jobs.STALE_TEMPORAL_RUNNING_TIMEOUT
        - timedelta(minutes=1)
    )
    monkeypatch.setattr(
        jobs._executor,
        "submit",
        lambda fn: types.SimpleNamespace(),
    )

    result = jobs.dispatch_video_temporal_inference(
        video_id=video.pk,
        model_meta_id=model_meta.pk,
    )

    stale_history.refresh_from_db()
    assert stale_history.status == VideoProcessingHistory.STATUS_FAILURE
    assert result.status == "queued"
    assert result.history_id != stale_history.pk
    assert not frame_dir.exists()
    state.refresh_from_db()
    assert state.frames_extracted is False
    assert not Frame.objects.filter(video=video, is_extracted=True).exists()


@pytest.mark.django_db(transaction=True)
def test_run_video_temporal_inference_redelivered_stream_success_preserves_frames(
    tmp_path,
):
    video = _create_video(tmp_path)
    model_meta, _label_a, _label_b = _create_model_meta()
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    frame_path = frame_dir / "frame_0000000.jpg"
    frame_path.write_bytes(b"frame")
    Frame.objects.create(
        video=video,
        frame_number=0,
        relative_path="frame_0000000.jpg",
        is_extracted=True,
    )
    state = video.get_or_create_state()
    state.frames_initialized = True
    state.frame_count = 1
    state.frames_extracted = True
    state.save(
        update_fields=[
            "frames_initialized",
            "frame_count",
            "frames_extracted",
        ]
    )
    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        status=VideoProcessingHistory.STATUS_SUCCESS,
        config=jobs._temporal_history_config(
            model_meta_id=model_meta.pk,
            replace_prediction_segments=True,
            delete_frames_after=True,
            ocr_frame_fraction=0.001,
            ocr_cap=10,
            temporal_options={},
            queue="inference",
            frame_source_mode="stream",
        ),
    )

    assert jobs._run_video_temporal_inference(
        video.pk,
        model_meta_id=model_meta.pk,
        history_id=history.pk,
        delete_frames_after=True,
    )

    assert frame_path.exists()
    state.refresh_from_db()
    assert state.frames_extracted is True
    assert Frame.objects.filter(video=video, is_extracted=True).exists()


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
    assert history.config["result"]["frame_source_mode"] == "stream"


@pytest.mark.django_db
def test_run_video_temporal_inference_stream_succeeds_when_extract_frames_would_fail(
    monkeypatch,
    tmp_path,
):
    video = _create_video(tmp_path)
    model_meta, label_a, _label_b = _create_model_meta()
    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        status=VideoProcessingHistory.STATUS_PENDING,
        config={"kind": jobs.TEMPORAL_INFERENCE_KIND},
    )

    monkeypatch.setattr(VideoFile, "update_video_meta", lambda self: None)
    monkeypatch.setattr(
        VideoFile,
        "extract_frames",
        lambda self, overwrite=False: (_ for _ in ()).throw(
            AssertionError("streaming temporal inference must not extract frames")
        ),
    )
    monkeypatch.setattr(
        VideoFile,
        "update_text_metadata",
        lambda self, **kwargs: (_ for _ in ()).throw(
            AssertionError("streaming temporal inference must not require frame OCR")
        ),
    )
    monkeypatch.setattr(
        jobs,
        "_has_extracted_frame_files",
        lambda video_obj: (_ for _ in ()).throw(
            AssertionError("streaming temporal inference must not inspect frame cache")
        ),
    )

    def _fake_predict_video(self, **kwargs):
        assert kwargs["return_frame_scores"] is True
        assert kwargs["frame_source_mode"] == "stream"
        assert kwargs["frame_source_file_type"] == "raw"
        return VideoFrameScoreResult(
            labels=[label_a.name],
            frame_scores=[[0.1], [0.9], [0.95]],
            device="cpu",
            frame_count=3,
            frame_numbers=[0, 1, 2],
            timestamps=[0.0, 0.04, 0.08],
        )

    monkeypatch.setattr(VideoFile, "predict_video", _fake_predict_video)

    def _fake_lx_core(**kwargs):
        score_result = kwargs["score_result"]
        assert score_result.frame_numbers == [0, 1, 2]
        assert score_result.timestamps == [0.0, 0.04, 0.08]
        return types.SimpleNamespace(
            temporal_segments=[
                types.SimpleNamespace(label=label_a.name, start_frame=1, end_frame=2)
            ],
            backend="torch",
            device="cpu",
            duration_ms=1.0,
            provenance={"local_only": True},
        )

    monkeypatch.setattr(jobs, "_run_lx_ai_core_temporal_inference", _fake_lx_core)

    assert jobs._run_video_temporal_inference(
        video.pk,
        model_meta_id=model_meta.pk,
        history_id=history.pk,
        delete_frames_after=True,
        frame_source_mode="stream",
    )

    history.refresh_from_db()
    assert history.status == VideoProcessingHistory.STATUS_SUCCESS
    assert history.config["result"]["score_frame_numbers_present"] is True
    assert history.config["result"]["score_timestamps_present"] is True
    assert video.get_frame_dir_path().exists()
    state = video.get_or_create_state()
    state.refresh_from_db()
    assert state.frames_extracted is False


@pytest.mark.django_db
def test_run_video_temporal_inference_stream_failure_does_not_create_frame_cache_state(
    monkeypatch,
    tmp_path,
):
    video = _create_video(tmp_path)
    model_meta, _label_a, _label_b = _create_model_meta()
    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        status=VideoProcessingHistory.STATUS_PENDING,
        config={"kind": jobs.TEMPORAL_INFERENCE_KIND},
    )

    monkeypatch.setattr(VideoFile, "update_video_meta", lambda self: None)
    monkeypatch.setattr(
        VideoFile,
        "extract_frames",
        lambda self, overwrite=False: (_ for _ in ()).throw(
            AssertionError("streaming temporal inference must not extract frames")
        ),
    )
    monkeypatch.setattr(
        VideoFile,
        "predict_video",
        lambda self, **kwargs: (_ for _ in ()).throw(
            RuntimeError("streaming decode failed")
        ),
    )

    with pytest.raises(RuntimeError, match="streaming decode failed"):
        jobs._run_video_temporal_inference(
            video.pk,
            model_meta_id=model_meta.pk,
            history_id=history.pk,
            delete_frames_after=True,
            frame_source_mode="stream",
        )

    history.refresh_from_db()
    assert history.status == VideoProcessingHistory.STATUS_FAILURE
    assert "streaming decode failed" in history.details
    assert video.get_frame_dir_path().exists()
    state = video.get_or_create_state()
    state.refresh_from_db()
    assert state.frames_extracted is False
    assert not Frame.objects.filter(video=video, is_extracted=True).exists()


@pytest.mark.django_db
def test_run_video_temporal_inference_auto_uses_cache_when_frame_cache_exists(
    monkeypatch,
    tmp_path,
):
    video = _create_video(tmp_path)
    model_meta, label_a, _label_b = _create_model_meta()
    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        status=VideoProcessingHistory.STATUS_PENDING,
        config={"kind": jobs.TEMPORAL_INFERENCE_KIND, "frame_source_mode": "auto"},
    )
    calls: list[str] = []

    monkeypatch.setattr(VideoFile, "update_video_meta", lambda self: None)
    monkeypatch.setattr(
        VideoFile,
        "extract_frames",
        lambda self, overwrite=False: calls.append("extract_frames") or True,
    )
    monkeypatch.setattr(
        VideoFile,
        "update_text_metadata",
        lambda self, **kwargs: calls.append("update_text_metadata") or True,
    )
    monkeypatch.setattr(jobs, "_has_extracted_frame_files", lambda video_obj: True)

    def _fake_predict_video(self, **kwargs):
        assert kwargs["frame_source_mode"] == "cache"
        return VideoFrameScoreResult(
            labels=[label_a.name],
            frame_scores=[[0.7]],
            device="cpu",
            frame_count=1,
        )

    monkeypatch.setattr(VideoFile, "predict_video", _fake_predict_video)
    monkeypatch.setattr(
        jobs,
        "_run_lx_ai_core_temporal_inference",
        lambda **kwargs: types.SimpleNamespace(
            temporal_segments=[],
            backend="torch",
            device="cpu",
            duration_ms=1.0,
            provenance={"local_only": True},
        ),
    )

    assert jobs._run_video_temporal_inference(
        video.pk,
        model_meta_id=model_meta.pk,
        history_id=history.pk,
        delete_frames_after=False,
        frame_source_mode="auto",
    )

    assert calls == ["extract_frames", "update_text_metadata"]
    history.refresh_from_db()
    assert history.config["requested_frame_source_mode"] == "auto"
    assert history.config["resolved_frame_source_mode"] == "cache"
    assert history.config["result"]["resolved_frame_source_mode"] == "cache"


@pytest.mark.django_db
def test_run_video_temporal_inference_auto_uses_stream_without_frame_cache(
    monkeypatch,
    tmp_path,
):
    video = _create_video(tmp_path)
    model_meta, label_a, _label_b = _create_model_meta()
    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        status=VideoProcessingHistory.STATUS_PENDING,
        config={"kind": jobs.TEMPORAL_INFERENCE_KIND, "frame_source_mode": "auto"},
    )

    monkeypatch.setattr(VideoFile, "update_video_meta", lambda self: None)
    monkeypatch.setattr(
        VideoFile,
        "extract_frames",
        lambda self, overwrite=False: (_ for _ in ()).throw(
            AssertionError("auto stream mode must not extract frames")
        ),
    )
    monkeypatch.setattr(
        VideoFile,
        "update_text_metadata",
        lambda self, **kwargs: (_ for _ in ()).throw(
            AssertionError("auto stream mode must not require frame OCR")
        ),
    )
    monkeypatch.setattr(jobs, "_has_extracted_frame_files", lambda video_obj: False)

    def _fake_predict_video(self, **kwargs):
        assert kwargs["frame_source_mode"] == "stream"
        return VideoFrameScoreResult(
            labels=[label_a.name],
            frame_scores=[[0.2]],
            device="cpu",
            frame_count=1,
            frame_numbers=[0],
            timestamps=[0.0],
        )

    monkeypatch.setattr(VideoFile, "predict_video", _fake_predict_video)
    monkeypatch.setattr(
        jobs,
        "_run_lx_ai_core_temporal_inference",
        lambda **kwargs: types.SimpleNamespace(
            temporal_segments=[],
            backend="torch",
            device="cpu",
            duration_ms=1.0,
            provenance={"local_only": True},
        ),
    )

    assert jobs._run_video_temporal_inference(
        video.pk,
        model_meta_id=model_meta.pk,
        history_id=history.pk,
        delete_frames_after=True,
        frame_source_mode="auto",
    )

    history.refresh_from_db()
    assert history.config["requested_frame_source_mode"] == "auto"
    assert history.config["resolved_frame_source_mode"] == "stream"
    assert history.config["result"]["resolved_frame_source_mode"] == "stream"


@pytest.mark.django_db
def test_run_video_temporal_inference_fails_when_current_meta_materializes_nothing(
    monkeypatch,
    tmp_path,
):
    video = _create_video(tmp_path)
    model_meta, label_a, label_b = _create_model_meta()
    prediction_source, _ = InformationSource.objects.get_or_create_by_name("prediction")
    LabelVideoSegment.objects.create(
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
    monkeypatch.setattr(
        VideoFile,
        "extract_frames",
        lambda self, overwrite=False: True,
    )
    monkeypatch.setattr(
        VideoFile,
        "update_text_metadata",
        lambda self, **kwargs: True,
    )
    monkeypatch.setattr(jobs, "_has_extracted_frame_files", lambda video_obj: True)
    monkeypatch.setattr(
        VideoFile,
        "predict_video",
        lambda self, **kwargs: VideoFrameScoreResult(
            labels=[label_a.name],
            frame_scores=[[0.8], [0.9], [0.7], [0.1]],
            device="cpu",
            frame_count=4,
        ),
    )
    monkeypatch.setattr(
        jobs,
        "_run_lx_ai_core_temporal_inference",
        lambda **kwargs: types.SimpleNamespace(
            temporal_segments=[
                types.SimpleNamespace(
                    label=f"missing-{uuid.uuid4().hex[:8]}",
                    start_frame=0,
                    end_frame=2,
                )
            ],
            backend="torch",
            device="cpu",
            duration_ms=1.5,
            provenance={"local_only": True},
        ),
    )

    with pytest.raises(RuntimeError, match="no LabelVideoSegment rows"):
        jobs._run_video_temporal_inference(
            video.pk,
            model_meta_id=model_meta.pk,
            history_id=history.pk,
            delete_frames_after=False,
        )

    assert not VideoPredictionMeta.objects.filter(
        video_file=video,
        model_meta=model_meta,
    ).exists()
    assert LabelVideoSegment.objects.filter(
        video_file=video,
        source=prediction_source,
    ).exists()

    state = video.get_or_create_state()
    state.refresh_from_db()
    assert state.initial_prediction_completed is False
    assert state.lvs_created is False
    history.refresh_from_db()
    assert history.status == VideoProcessingHistory.STATUS_FAILURE


@pytest.mark.django_db(transaction=True)
def test_run_video_temporal_inference_rolls_back_frames_on_failure(
    monkeypatch,
    tmp_path,
):
    video = _create_video(tmp_path)
    model_meta, _label_a, _label_b = _create_model_meta()
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        status=VideoProcessingHistory.STATUS_PENDING,
        config={"kind": jobs.TEMPORAL_INFERENCE_KIND},
    )

    monkeypatch.setattr(VideoFile, "update_video_meta", lambda self: None)

    def fake_extract_frames(self, overwrite=False):
        frame_dir.mkdir(parents=True, exist_ok=True)
        (frame_dir / "frame_0000000.jpg").write_bytes(b"frame")
        Frame.objects.update_or_create(
            video=self,
            frame_number=0,
            defaults={
                "relative_path": "frame_0000000.jpg",
                "is_extracted": True,
            },
        )
        state = self.get_or_create_state()
        state.frames_initialized = True
        state.frame_count = 1
        state.frames_extracted = True
        state.save(
            update_fields=[
                "frames_initialized",
                "frame_count",
                "frames_extracted",
            ]
        )
        return True

    monkeypatch.setattr(VideoFile, "extract_frames", fake_extract_frames)
    monkeypatch.setattr(
        VideoFile,
        "update_text_metadata",
        lambda self, **kwargs: True,
    )
    monkeypatch.setattr(jobs, "_has_extracted_frame_files", lambda video_obj: True)
    monkeypatch.setattr(
        VideoFile,
        "predict_video",
        lambda self, **kwargs: (_ for _ in ()).throw(RuntimeError("prediction failed")),
    )

    with pytest.raises(RuntimeError, match="prediction failed"):
        jobs._run_video_temporal_inference(
            video.pk,
            model_meta_id=model_meta.pk,
            history_id=history.pk,
            delete_frames_after=True,
            frame_source_mode="cache",
        )

    history.refresh_from_db()
    assert history.status == VideoProcessingHistory.STATUS_FAILURE
    assert not frame_dir.exists()
    state = video.get_or_create_state()
    state.refresh_from_db()
    assert state.frames_extracted is False
    assert not Frame.objects.filter(video=video, is_extracted=True).exists()


@pytest.mark.django_db(transaction=True)
def test_run_video_temporal_inference_cleans_frames_for_redelivered_success(
    tmp_path,
):
    video = _create_video(tmp_path)
    model_meta, _label_a, _label_b = _create_model_meta()
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    (frame_dir / "frame_0000000.jpg").write_bytes(b"frame")
    Frame.objects.create(
        video=video,
        frame_number=0,
        relative_path="frame_0000000.jpg",
        is_extracted=True,
    )
    state = video.get_or_create_state()
    state.frames_initialized = True
    state.frame_count = 1
    state.frames_extracted = True
    state.save(
        update_fields=[
            "frames_initialized",
            "frame_count",
            "frames_extracted",
        ]
    )
    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        status=VideoProcessingHistory.STATUS_SUCCESS,
        config=jobs._temporal_history_config(
            model_meta_id=model_meta.pk,
            replace_prediction_segments=True,
            delete_frames_after=True,
            ocr_frame_fraction=0.001,
            ocr_cap=10,
            temporal_options={},
            queue="inference",
            frame_source_mode="cache",
        ),
    )

    assert jobs._run_video_temporal_inference(
        video.pk,
        model_meta_id=model_meta.pk,
        history_id=history.pk,
        delete_frames_after=True,
    )

    assert not frame_dir.exists()
    state.refresh_from_db()
    assert state.frames_extracted is False
    assert not Frame.objects.filter(video=video, is_extracted=True).exists()

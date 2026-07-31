from __future__ import annotations

import types
import uuid
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import NoReturn, TypedDict, Unpack
from unittest.mock import Mock

import numpy as np
import pytest
from django.utils import timezone
from numpy.typing import NDArray
from pytest import MonkeyPatch

from endoreg_db.models.administration.ai.ai_model import AiModel
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.services.video_files._ai import VideoFrameScoreResult
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.models.metadata.model_meta import ModelMeta
from endoreg_db.models.metadata.video_prediction_meta import VideoPredictionMeta
from endoreg_db.models.other.information_source import InformationSource
from endoreg_db.services import video_temporal_inference as jobs
from lx_dtypes.models.contracts.video_temporal_inference import (
    TemporalInferenceHistoryResultPayload,
    parse_temporal_inference_history_config_payload,
)


class _PredictVideoKwargs(TypedDict, total=False):
    return_frame_scores: bool
    frame_source_mode: str
    frame_source_file_type: str


class _TextMetadataKwargs(TypedDict, total=False):
    ocr_frame_fraction: float
    ocr_cap: int
    frame_source_mode: str


class _LxTemporalOptions(TypedDict, total=False):
    include_score_vectors: bool


class _LxCoreKwargs(TypedDict, total=False):
    lx_options: _LxTemporalOptions
    score_result: VideoFrameScoreResult


def _score_array(rows: list[list[float]]) -> NDArray[np.float64]:
    return np.asarray(rows, dtype=np.float64)


def _raise_runtime(error: RuntimeError) -> NoReturn:
    raise error


def _update_video_meta_noop(video_obj: VideoFile) -> None:
    return None


def _update_video_text_metadata_success(
    video_obj: VideoFile, **kwargs: Unpack[_TextMetadataKwargs]
) -> bool:
    return True


def _has_extracted_frame_files_true(video_obj: VideoFile) -> bool:
    return True


def _submit_noop(fn: Callable[[], bool]) -> types.SimpleNamespace:
    return types.SimpleNamespace()


def _extract_video_frames_success(
    video_obj: VideoFile, overwrite: bool = False
) -> bool:
    return True


def _extract_video_frames_forbidden(
    video_obj: VideoFile, overwrite: bool = False
) -> NoReturn:
    _raise_runtime(RuntimeError("temporal inference must not extract frames"))


def _update_video_text_metadata_forbidden(
    video_obj: VideoFile, **kwargs: Unpack[_TextMetadataKwargs]
) -> NoReturn:
    _raise_runtime(RuntimeError("temporal inference must not require frame OCR"))


def _has_extracted_frame_files_forbidden(video_obj: VideoFile) -> NoReturn:
    _raise_runtime(RuntimeError("temporal inference must not inspect frame cache"))


def _predict_video_streaming_decode_failure(
    video_obj: VideoFile, **kwargs: Unpack[_PredictVideoKwargs]
) -> NoReturn:
    _raise_runtime(RuntimeError("streaming decode failed"))


def _predict_video_prediction_failure(
    video_obj: VideoFile, **kwargs: Unpack[_PredictVideoKwargs]
) -> NoReturn:
    _raise_runtime(RuntimeError("prediction failed"))


def _lx_core_empty(**kwargs: Unpack[_LxCoreKwargs]) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        temporal_segments=[],
        backend="torch",
        device="cpu",
        duration_ms=1.0,
        provenance={"local_only": True},
    )


def _history_result(
    history: VideoProcessingHistory,
) -> TemporalInferenceHistoryResultPayload:
    config = parse_temporal_inference_history_config_payload(history.config)
    result = config.result
    assert result is not None
    return result


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
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"temporal-video-{uuid.uuid4().hex}",
        frame_count=4,
        duration=0.16,
        frame_dir=str(frame_dir),
        fps=25.0,
    )
    Frame.objects.bulk_create(
        [
            Frame(
                video=video,
                frame_number=index,
                timestamp=index * 0.04,
                relative_path=f"frame_{index:07d}.jpg",
            )
            for index in range(4)
        ]
    )
    return video


def test_build_lx_temporal_options_defaults_use_presentation_time_domain():
    lx_options, history_options = jobs.build_lx_temporal_options({})

    assert lx_options["temporal_model"] == "hysteresis"
    assert lx_options["threshold"] == 0.5
    assert lx_options["min_length"] == 1
    assert lx_options["max_gap"] == 0
    assert lx_options["smoothing_window"] == 1
    assert lx_options["include_score_vectors"] is False
    assert history_options["coordinate_basis"] == "presentation_timestamps"
    assert history_options["smoothing_window_seconds"] == 1.0
    assert history_options["temporal_smoothing_enabled"] is True


def test_build_lx_temporal_options_can_disable_temporal_smoothing():
    lx_options, history_options = jobs.build_lx_temporal_options(
        {
            "temporal_smoothing_enabled": False,
            "smoothing_window_seconds": 3.0,
        },
    )

    assert lx_options["smoothing_window"] == 1
    assert history_options["smoothing_window_seconds"] == 0.0
    assert history_options["temporal_smoothing_enabled"] is False


def test_build_lx_temporal_options_rejects_invalid_smoothing_enabled_value():
    with pytest.raises(
        jobs.TemporalInferenceConfigError,
        match="temporal_smoothing_enabled must be a boolean",
    ):
        jobs.build_lx_temporal_options(
            {"temporal_smoothing_enabled": "sometimes"},
        )


def test_build_lx_temporal_options_accepts_label_keyed_thresholds():
    lx_options, _ = jobs.build_lx_temporal_options(
        {
            "threshold": {"polyp": 0.7, "outside": 0.4},
            "low_threshold": {"polyp": 0.55},
            "min_length_seconds": 0.01,
        },
    )

    assert lx_options["threshold"] == 0.5
    assert lx_options["thresholds"] == {"polyp": 0.7, "outside": 0.4}
    assert lx_options["low_thresholds"] == {"polyp": 0.55}
    assert lx_options["min_length"] == 1


def test_build_lx_temporal_options_rejects_invalid_model():
    with pytest.raises(jobs.TemporalInferenceConfigError):
        jobs.build_lx_temporal_options({"temporal_model": "unknown"})


def test_temporal_options_boundary_normalizes_legacy_mapping_once():
    options = jobs.normalize_temporal_options(
        {
            "temporal_model": " Markov ",
            "min_length_seconds": "2.5",
            "temporal_smoothing_enabled": "false",
        }
    )

    assert isinstance(options, jobs.CanonicalTemporalOptions)
    assert options.min_length_seconds == 2.5
    assert options.smoothing_window_seconds == 0.0
    assert options.temporal_smoothing_enabled is False
    assert options.lx_options["temporal_model"] == "markov"
    assert jobs.normalize_temporal_options(options) is options


def test_temporal_options_boundary_roundtrips_only_canonical_shape():
    canonical = jobs.normalize_temporal_options(
        {"temporal_model": "viterbi", "max_gap_seconds": 0.25}
    )

    reparsed = jobs.normalize_temporal_options(canonical.to_dict())

    assert reparsed == canonical
    assert set(reparsed.to_dict()) == {
        "coordinate_basis",
        "min_length_seconds",
        "max_gap_seconds",
        "smoothing_window_seconds",
        "temporal_smoothing_enabled",
        "lx_options",
    }


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"unexpected_option": True}, "unknown temporal options"),
        ({"threshold": float("nan")}, "threshold must be finite"),
        (
            {
                "coordinate_basis": "presentation_timestamps",
                "min_length_seconds": "1.0",
                "max_gap_seconds": 0.0,
                "smoothing_window_seconds": 1.0,
                "temporal_smoothing_enabled": True,
                "lx_options": {},
            },
            "valid number",
        ),
    ],
)
def test_temporal_options_boundary_rejects_noncanonical_input(
    payload: dict[str, object],
    message: str,
):
    with pytest.raises(jobs.TemporalInferenceConfigError, match=message):
        jobs.normalize_temporal_options(payload)


def test_temporal_history_parser_upgrades_raw_options_to_canonical_shape():
    config = jobs._parse_temporal_history_config(
        {
            "kind": jobs.TEMPORAL_INFERENCE_KIND,
            "model_meta_id": 7,
            "queue": "inference",
            "temporal_options": jobs.normalize_temporal_options({}).to_dict(),
            "raw_temporal_options": {"temporal_model": "markov"},
        }
    )

    assert config is not None
    assert config.temporal_options.lx_options["temporal_model"] == "markov"
    assert "raw_temporal_options" not in config.to_dict()


def test_extract_temporal_options_accepts_known_api_envelope_only():
    assert jobs.extract_temporal_options(
        {
            "model_meta_id": 7,
            "replace_prediction_segments": True,
            "temporal_model": "markov",
            "threshold": 0.7,
        }
    ) == {"temporal_model": "markov", "threshold": 0.7}


def test_extract_temporal_options_rejects_unknown_api_field():
    with pytest.raises(
        jobs.TemporalInferenceConfigError,
        match="unknown temporal request options: typo_threshold",
    ):
        jobs.extract_temporal_options({"typo_threshold": 0.7})


def test_run_job_boundary_rejects_invalid_options_before_database_access(
    monkeypatch: MonkeyPatch,
):
    history_lookup = Mock(side_effect=AssertionError("database must not be read"))
    monkeypatch.setattr(jobs, "_get_processing_history", history_lookup)

    with pytest.raises(
        jobs.TemporalInferenceConfigError,
        match="unknown temporal options: typo_threshold",
    ):
        jobs._run_video_temporal_inference(
            1,
            model_meta_id=1,
            temporal_options={"typo_threshold": 0.7},
        )

    history_lookup.assert_not_called()


def test_smooth_scores_uses_elapsed_presentation_time():
    score_result = VideoFrameScoreResult(
        labels=["finding"],
        frame_scores=_score_array([[0.0], [1.0], [0.0], [1.0]]),
        device="cpu",
        frame_count=4,
        frame_numbers=[0, 1, 2, 3],
        timestamps=[0.0, 0.1, 0.9, 1.0],
    )
    timeline = jobs.TemporalScoreTimeline(
        frame_numbers=(0, 1, 2, 3),
        timestamps=(0.0, 0.1, 0.9, 1.0),
        terminal_frame_number=4,
        terminal_timestamp=1.1,
    )

    smoothed = jobs._smooth_scores_by_presentation_time(
        score_result,
        timeline,
        window_seconds=0.4,
    )

    assert smoothed.frame_scores.tolist() == [[0.5], [0.5], [0.5], [0.5]]


def test_segments_use_presentation_time_for_gap_and_minimum_duration():
    timeline = jobs.TemporalScoreTimeline(
        frame_numbers=(10, 11, 12, 13),
        timestamps=(0.0, 0.1, 0.5, 0.6),
        terminal_frame_number=14,
        terminal_timestamp=1.0,
    )

    sequences = jobs._segments_to_sequences(
        [
            types.SimpleNamespace(label="finding", start_frame=0, end_frame=0),
            types.SimpleNamespace(label="finding", start_frame=2, end_frame=3),
        ],
        timeline=timeline,
        min_length_seconds=0.9,
        max_gap_seconds=0.4,
    )

    assert sequences == {"finding": [(10, 14)]}


@pytest.mark.django_db
def test_resolve_score_timeline_rejects_non_increasing_timestamps(tmp_path: Path):
    video = _create_video(tmp_path)
    score_result = VideoFrameScoreResult(
        labels=["finding"],
        frame_scores=_score_array([[0.0], [1.0], [0.0]]),
        device="cpu",
        frame_count=3,
        frame_numbers=[0, 1, 2],
        timestamps=[0.0, 0.1, 0.1],
    )

    with pytest.raises(
        jobs.TemporalInferenceConfigError,
        match="strictly increasing",
    ):
        jobs._resolve_score_timeline(video, score_result)


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
def test_dispatch_video_temporal_inference_uses_inference_queue(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
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
    task_options = fake_task.apply_async.call_args.kwargs["kwargs"]["temporal_options"]
    history = VideoProcessingHistory.objects.get(pk=result.history_id)
    history_config = parse_temporal_inference_history_config_payload(history.config)
    assert history.operation == VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE
    assert history_config.kind == jobs.TEMPORAL_INFERENCE_KIND
    assert history_config.frame_source_mode == "stream"
    assert task_options == history_config.temporal_options
    assert "raw_temporal_options" not in history.config


@pytest.mark.django_db
def test_dispatch_video_temporal_inference_reuses_active_history(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    video = _create_video(tmp_path)
    model_meta, _label_a, _label_b = _create_model_meta()
    submitted: list[Callable[[], bool]] = []

    def submit_once(fn: Callable[[], bool]) -> types.SimpleNamespace:
        submitted.append(fn)
        return types.SimpleNamespace()

    monkeypatch.setenv("VIDEO_TEMPORAL_INFERENCE_JOB_MODE", "thread")
    monkeypatch.setattr(
        jobs._executor,
        "submit",
        submit_once,
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
def test_dispatch_video_temporal_inference_defers_for_blackening_rebuild(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
):
    video = _create_video(tmp_path)
    model_meta, _label_a, _label_b = _create_model_meta()
    monkeypatch.setenv("VIDEO_TEMPORAL_INFERENCE_JOB_MODE", "thread")
    submitted = Mock(side_effect=AssertionError("deferred inference must not queue"))
    monkeypatch.setattr(jobs._executor, "submit", submitted)
    rebuild_history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        config={"kind": "outside_frame_blackening"},
    )

    result = jobs.dispatch_video_temporal_inference(
        video_id=video.pk,
        model_meta_id=model_meta.pk,
        temporal_options={"temporal_model": "markov"},
        delete_frames_after=False,
    )

    assert result.status == jobs.TEMPORAL_INFERENCE_STATUS_PENDING_AFTER_REBUILD
    assert result.task_id == ""
    assert result.reason == jobs.TEMPORAL_INFERENCE_DEFERRED_REASON_REPROCESSING
    assert result.blocked_by_history_id == rebuild_history.pk
    submitted.assert_not_called()
    history = VideoProcessingHistory.objects.get(pk=result.history_id)
    history_config = parse_temporal_inference_history_config_payload(history.config)
    assert history.operation == VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE
    assert history.status == VideoProcessingHistory.STATUS_PENDING
    assert history.task_id == ""
    assert (
        history_config.deferred_reason
        == jobs.TEMPORAL_INFERENCE_DEFERRED_REASON_REPROCESSING
    )
    assert history_config.blocked_by_history_id == rebuild_history.pk
    canonical_options = jobs.normalize_temporal_options(history_config.temporal_options)
    assert canonical_options.lx_options["temporal_model"] == "markov"
    assert "raw_temporal_options" not in history.config
    assert history_config.delete_frames_after is False


@pytest.mark.django_db
def test_dispatch_video_temporal_inference_reuses_same_deferred_request(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
):
    video = _create_video(tmp_path)
    model_meta, _label_a, _label_b = _create_model_meta()
    monkeypatch.setenv("VIDEO_TEMPORAL_INFERENCE_JOB_MODE", "thread")
    monkeypatch.setattr(
        jobs._executor,
        "submit",
        Mock(side_effect=AssertionError("deferred inference must not queue")),
    )
    VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        config={"kind": "outside_frame_blackening"},
    )

    result = jobs.dispatch_video_temporal_inference(
        video_id=video.pk,
        model_meta_id=model_meta.pk,
        temporal_options={"temporal_model": "markov"},
    )
    second = jobs.dispatch_video_temporal_inference(
        video_id=video.pk,
        model_meta_id=model_meta.pk,
        temporal_options={"temporal_model": "markov"},
    )

    assert result.status == jobs.TEMPORAL_INFERENCE_STATUS_PENDING_AFTER_REBUILD
    assert second.status == jobs.TEMPORAL_INFERENCE_STATUS_PENDING_AFTER_REBUILD
    assert second.history_id == result.history_id
    assert (
        VideoProcessingHistory.objects.filter(
            video=video,
            operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_dispatch_video_temporal_inference_latest_deferred_request_wins(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
):
    video = _create_video(tmp_path)
    model_meta, _label_a, _label_b = _create_model_meta()
    monkeypatch.setenv("VIDEO_TEMPORAL_INFERENCE_JOB_MODE", "thread")
    monkeypatch.setattr(
        jobs._executor,
        "submit",
        Mock(side_effect=AssertionError("deferred inference must not queue")),
    )
    VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        config={"kind": "outside_frame_blackening"},
    )

    first = jobs.dispatch_video_temporal_inference(
        video_id=video.pk,
        model_meta_id=model_meta.pk,
        temporal_options={"temporal_model": "markov"},
    )
    second = jobs.dispatch_video_temporal_inference(
        video_id=video.pk,
        model_meta_id=model_meta.pk,
        temporal_options={"temporal_model": "hysteresis"},
    )

    assert first.status == jobs.TEMPORAL_INFERENCE_STATUS_PENDING_AFTER_REBUILD
    assert second.status == jobs.TEMPORAL_INFERENCE_STATUS_PENDING_AFTER_REBUILD
    assert second.history_id != first.history_id
    old_history = VideoProcessingHistory.objects.get(pk=first.history_id)
    new_history = VideoProcessingHistory.objects.get(pk=second.history_id)
    new_history_config = parse_temporal_inference_history_config_payload(
        new_history.config
    )
    assert old_history.status == VideoProcessingHistory.STATUS_CANCELLED
    assert new_history.status == VideoProcessingHistory.STATUS_PENDING
    canonical_options = jobs.normalize_temporal_options(
        new_history_config.temporal_options
    )
    assert canonical_options.lx_options["temporal_model"] == "hysteresis"
    assert "raw_temporal_options" not in new_history.config


@pytest.mark.django_db
def test_dispatch_video_temporal_inference_busy_for_non_blackening_reprocessing(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
):
    video = _create_video(tmp_path)
    model_meta, _label_a, _label_b = _create_model_meta()
    monkeypatch.setenv("VIDEO_TEMPORAL_INFERENCE_JOB_MODE", "thread")
    reprocessing_history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        config={"kind": "other_reprocessing"},
    )

    result = jobs.dispatch_video_temporal_inference(
        video_id=video.pk,
        model_meta_id=model_meta.pk,
    )

    assert result.status == "busy"
    assert result.blocked_by_history_id == reprocessing_history.pk
    assert (
        VideoProcessingHistory.objects.filter(
            video=video,
            operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        ).count()
        == 0
    )


@pytest.mark.django_db(transaction=True)
def test_dispatch_video_temporal_inference_expires_stale_running_history_and_rolls_back_frames(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
):
    video = _create_video(tmp_path)
    model_meta, _label_a, _label_b = _create_model_meta()
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    (frame_dir / "frame_0000000.jpg").write_bytes(b"partial")
    Frame.objects.update_or_create(
        video=video,
        frame_number=0,
        defaults={
            "relative_path": "frame_0000000.jpg",
            "is_extracted": True,
        },
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
        _submit_noop,
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
    tmp_path: Path,
):
    video = _create_video(tmp_path)
    model_meta, _label_a, _label_b = _create_model_meta()
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    frame_path = frame_dir / "frame_0000000.jpg"
    frame_path.write_bytes(b"frame")
    Frame.objects.update_or_create(
        video=video,
        frame_number=0,
        defaults={
            "relative_path": "frame_0000000.jpg",
            "is_extracted": True,
        },
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
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
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
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
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

    monkeypatch.setattr(jobs, "update_video_meta", _update_video_meta_noop)
    monkeypatch.setattr(
        jobs,
        "extract_video_frames",
        _extract_video_frames_success,
    )
    monkeypatch.setattr(
        jobs,
        "update_video_text_metadata",
        _update_video_text_metadata_success,
    )
    monkeypatch.setattr(
        jobs, "_has_extracted_frame_files", _has_extracted_frame_files_true
    )

    def _fake_predict_video(
        self: VideoFile, **kwargs: Unpack[_PredictVideoKwargs]
    ) -> VideoFrameScoreResult:
        assert kwargs.get("return_frame_scores") is True
        return VideoFrameScoreResult(
            labels=[label_a.name, label_b.name],
            frame_scores=_score_array(
                [
                    [0.1, 0.8],
                    [0.2, 0.9],
                    [0.9, 0.1],
                    [0.95, 0.2],
                ]
            ),
            device="cpu",
            frame_count=4,
        )

    monkeypatch.setattr(jobs, "predict_video", _fake_predict_video)

    def _fake_lx_core(**kwargs: Unpack[_LxCoreKwargs]) -> types.SimpleNamespace:
        lx_options = kwargs.get("lx_options")
        assert lx_options is not None
        assert lx_options.get("include_score_vectors") is False
        effective_scores = kwargs.get("score_result")
        assert effective_scores is not None
        assert effective_scores.frame_numbers == [0, 1, 2, 3]
        assert effective_scores.timestamps == [0.0, 0.04, 0.08, 0.12]
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
        temporal_options={"min_length_seconds": 0.0},
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
    result_payload = _history_result(history)
    assert history.status == VideoProcessingHistory.STATUS_SUCCESS
    assert result_payload.score_vectors_stored is False
    assert result_payload.deleted_prediction_segments == 1
    assert result_payload.frame_source_mode == "stream"


@pytest.mark.django_db
def test_run_video_temporal_inference_stream_succeeds_when_extract_frames_would_fail(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
):
    video = _create_video(tmp_path)
    model_meta, label_a, _label_b = _create_model_meta()
    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        status=VideoProcessingHistory.STATUS_PENDING,
        config={"kind": jobs.TEMPORAL_INFERENCE_KIND},
    )

    monkeypatch.setattr(jobs, "update_video_meta", _update_video_meta_noop)
    monkeypatch.setattr(
        jobs,
        "extract_video_frames",
        _extract_video_frames_forbidden,
    )
    monkeypatch.setattr(
        jobs,
        "update_video_text_metadata",
        _update_video_text_metadata_forbidden,
    )
    monkeypatch.setattr(
        jobs,
        "_has_extracted_frame_files",
        _has_extracted_frame_files_forbidden,
    )

    def _fake_predict_video(
        self: VideoFile, **kwargs: Unpack[_PredictVideoKwargs]
    ) -> VideoFrameScoreResult:
        assert kwargs.get("return_frame_scores") is True
        assert kwargs.get("frame_source_mode") == "stream"
        assert kwargs.get("frame_source_file_type") == "raw"
        return VideoFrameScoreResult(
            labels=[label_a.name],
            frame_scores=_score_array([[0.1], [0.9], [0.95]]),
            device="cpu",
            frame_count=3,
            frame_numbers=[0, 1, 2],
            timestamps=[0.0, 0.04, 0.08],
        )

    monkeypatch.setattr(jobs, "predict_video", _fake_predict_video)

    def _fake_lx_core(**kwargs: Unpack[_LxCoreKwargs]) -> types.SimpleNamespace:
        score_result = kwargs.get("score_result")
        assert score_result is not None
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
    result_payload = _history_result(history)
    assert history.status == VideoProcessingHistory.STATUS_SUCCESS
    assert result_payload.score_frame_numbers_present is True
    assert result_payload.score_timestamps_present is True
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    assert frame_dir.exists()
    state = video.get_or_create_state()
    state.refresh_from_db()
    assert state.frames_extracted is False


@pytest.mark.django_db
def test_run_video_temporal_inference_stream_failure_does_not_create_frame_cache_state(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
):
    video = _create_video(tmp_path)
    model_meta, _label_a, _label_b = _create_model_meta()
    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        status=VideoProcessingHistory.STATUS_PENDING,
        config={"kind": jobs.TEMPORAL_INFERENCE_KIND},
    )

    monkeypatch.setattr(jobs, "update_video_meta", _update_video_meta_noop)
    monkeypatch.setattr(
        jobs,
        "extract_video_frames",
        _extract_video_frames_forbidden,
    )
    monkeypatch.setattr(
        jobs,
        "predict_video",
        _predict_video_streaming_decode_failure,
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
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    assert frame_dir.exists()
    state = video.get_or_create_state()
    state.refresh_from_db()
    assert state.frames_extracted is False
    assert not Frame.objects.filter(video=video, is_extracted=True).exists()


@pytest.mark.django_db
def test_run_video_temporal_inference_explicit_cache_uses_frame_cache(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
):
    video = _create_video(tmp_path)
    model_meta, label_a, _label_b = _create_model_meta()
    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        status=VideoProcessingHistory.STATUS_PENDING,
        config={"kind": jobs.TEMPORAL_INFERENCE_KIND, "frame_source_mode": "cache"},
    )
    calls: list[str] = []

    def extract_video_frames_cache(
        video_obj: VideoFile, overwrite: bool = False
    ) -> bool:
        calls.append("extract_frames")
        return True

    def update_video_text_metadata_cache(
        video_obj: VideoFile, **kwargs: Unpack[_TextMetadataKwargs]
    ) -> bool:
        calls.append("update_text_metadata")
        return True

    monkeypatch.setattr(jobs, "update_video_meta", _update_video_meta_noop)
    monkeypatch.setattr(
        jobs,
        "extract_video_frames",
        extract_video_frames_cache,
    )
    monkeypatch.setattr(
        jobs,
        "update_video_text_metadata",
        update_video_text_metadata_cache,
    )
    monkeypatch.setattr(
        jobs, "_has_extracted_frame_files", _has_extracted_frame_files_true
    )

    def _fake_predict_video(
        self: VideoFile, **kwargs: Unpack[_PredictVideoKwargs]
    ) -> VideoFrameScoreResult:
        assert kwargs.get("frame_source_mode") == "cache"
        return VideoFrameScoreResult(
            labels=[label_a.name],
            frame_scores=_score_array([[0.7]]),
            device="cpu",
            frame_count=1,
        )

    monkeypatch.setattr(jobs, "predict_video", _fake_predict_video)
    monkeypatch.setattr(
        jobs,
        "_run_lx_ai_core_temporal_inference",
        _lx_core_empty,
    )

    assert jobs._run_video_temporal_inference(
        video.pk,
        model_meta_id=model_meta.pk,
        history_id=history.pk,
        delete_frames_after=False,
        frame_source_mode="cache",
    )

    assert calls == ["extract_frames", "update_text_metadata"]
    history.refresh_from_db()
    history_config = parse_temporal_inference_history_config_payload(history.config)
    result_payload = _history_result(history)
    assert history_config.requested_frame_source_mode == "cache"
    assert history_config.resolved_frame_source_mode == "cache"
    assert result_payload.resolved_frame_source_mode == "cache"


@pytest.mark.django_db
def test_run_video_temporal_inference_auto_uses_stream_even_when_frame_cache_exists(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
):
    video = _create_video(tmp_path)
    model_meta, label_a, _label_b = _create_model_meta()
    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        status=VideoProcessingHistory.STATUS_PENDING,
        config={"kind": jobs.TEMPORAL_INFERENCE_KIND, "frame_source_mode": "auto"},
    )

    monkeypatch.setattr(jobs, "update_video_meta", _update_video_meta_noop)
    monkeypatch.setattr(
        jobs,
        "extract_video_frames",
        _extract_video_frames_forbidden,
    )
    monkeypatch.setattr(
        jobs,
        "update_video_text_metadata",
        _update_video_text_metadata_forbidden,
    )
    monkeypatch.setattr(
        jobs, "_has_extracted_frame_files", _has_extracted_frame_files_true
    )

    def _fake_predict_video(
        self: VideoFile, **kwargs: Unpack[_PredictVideoKwargs]
    ) -> VideoFrameScoreResult:
        assert kwargs.get("frame_source_mode") == "stream"
        return VideoFrameScoreResult(
            labels=[label_a.name],
            frame_scores=_score_array([[0.2]]),
            device="cpu",
            frame_count=1,
            frame_numbers=[0],
            timestamps=[0.0],
        )

    monkeypatch.setattr(jobs, "predict_video", _fake_predict_video)
    monkeypatch.setattr(
        jobs,
        "_run_lx_ai_core_temporal_inference",
        _lx_core_empty,
    )

    assert jobs._run_video_temporal_inference(
        video.pk,
        model_meta_id=model_meta.pk,
        history_id=history.pk,
        delete_frames_after=True,
        frame_source_mode="auto",
    )

    history.refresh_from_db()
    history_config = parse_temporal_inference_history_config_payload(history.config)
    result_payload = _history_result(history)
    assert history_config.requested_frame_source_mode == "auto"
    assert history_config.resolved_frame_source_mode == "stream"
    assert result_payload.resolved_frame_source_mode == "stream"


@pytest.mark.django_db
def test_run_video_temporal_inference_fails_when_current_meta_materializes_nothing(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
):
    video = _create_video(tmp_path)
    model_meta, label_a, label_b = _create_model_meta()
    prediction_source = InformationSource.objects.create(
        name=f"prediction-{uuid.uuid4().hex[:8]}"
    )
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

    monkeypatch.setattr(jobs, "update_video_meta", _update_video_meta_noop)
    monkeypatch.setattr(
        jobs,
        "extract_video_frames",
        _extract_video_frames_success,
    )
    monkeypatch.setattr(
        jobs,
        "update_video_text_metadata",
        _update_video_text_metadata_success,
    )
    monkeypatch.setattr(
        jobs, "_has_extracted_frame_files", _has_extracted_frame_files_true
    )

    def predict_video_missing_label(
        video_obj: VideoFile, **kwargs: Unpack[_PredictVideoKwargs]
    ) -> VideoFrameScoreResult:
        return VideoFrameScoreResult(
            labels=[label_a.name],
            frame_scores=_score_array([[0.8], [0.9], [0.7], [0.1]]),
            device="cpu",
            frame_count=4,
        )

    def lx_core_missing_label(**kwargs: Unpack[_LxCoreKwargs]) -> types.SimpleNamespace:
        return types.SimpleNamespace(
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
        )

    monkeypatch.setattr(
        jobs,
        "predict_video",
        predict_video_missing_label,
    )
    monkeypatch.setattr(
        jobs,
        "_run_lx_ai_core_temporal_inference",
        lx_core_missing_label,
    )

    with pytest.raises(RuntimeError, match="no LabelVideoSegment rows"):
        jobs._run_video_temporal_inference(
            video.pk,
            model_meta_id=model_meta.pk,
            history_id=history.pk,
            delete_frames_after=False,
            temporal_options={"min_length_seconds": 0.0},
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
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
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

    monkeypatch.setattr(jobs, "update_video_meta", _update_video_meta_noop)

    def fake_extract_frames(video_obj: VideoFile, overwrite: bool = False) -> bool:
        frame_dir.mkdir(parents=True, exist_ok=True)
        (frame_dir / "frame_0000000.jpg").write_bytes(b"frame")
        Frame.objects.update_or_create(
            video=video_obj,
            frame_number=0,
            defaults={
                "relative_path": "frame_0000000.jpg",
                "is_extracted": True,
            },
        )
        state = video_obj.get_or_create_state()
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

    monkeypatch.setattr(jobs, "extract_video_frames", fake_extract_frames)
    monkeypatch.setattr(
        jobs,
        "update_video_text_metadata",
        _update_video_text_metadata_success,
    )
    monkeypatch.setattr(
        jobs, "_has_extracted_frame_files", _has_extracted_frame_files_true
    )
    monkeypatch.setattr(
        jobs,
        "predict_video",
        _predict_video_prediction_failure,
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
    tmp_path: Path,
):
    video = _create_video(tmp_path)
    model_meta, _label_a, _label_b = _create_model_meta()
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    (frame_dir / "frame_0000000.jpg").write_bytes(b"frame")
    Frame.objects.update_or_create(
        video=video,
        frame_number=0,
        defaults={
            "relative_path": "frame_0000000.jpg",
            "is_extracted": True,
        },
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

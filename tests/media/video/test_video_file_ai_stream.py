# pyright: reportPrivateUsage=false
from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any, Protocol, cast

import pytest
import numpy as np
import torch
from pytest import MonkeyPatch
from django.core.files.base import ContentFile, File
from lx_dtypes.models.contracts.ai_prediction import AiPredictionConfigPayload

from endoreg_db.models import AiModel, Center, Label, LabelSet, ModelMeta, VideoFile
from endoreg_db.services.video_files import _ai as video_file_ai
from endoreg_db.services.video_files._ai import VideoFrameScoreResult


class _WritableFieldFile(Protocol):
    def save(self, name: str, content: File[bytes], save: bool = True) -> None: ...


class FakeModel:
    def to(self, device: object) -> "FakeModel":
        return self

    def eval(self) -> "FakeModel":
        return self


class FakeMultilabelNet:
    @classmethod
    def load_from_checkpoint(
        cls,
        *args: object,
        **kwargs: object,
    ) -> FakeModel:
        return FakeModel()


def test_remap_prediction_dict_preserves_score_list_shape() -> None:
    predictions = {
        "polyp": [0.2],
        "grasper": [0.7],
        "needle": [0.4],
    }
    mapping = {
        "polyp": ["polyp"],
        "instrument": ["grasper", "needle"],
        "outside": ["outside"],
    }

    result = video_file_ai._remap_prediction_dict(predictions, mapping)

    assert result == {
        "polyp": [0.2],
        "instrument": [0.7],
        "outside": [0.0],
    }


def test_remap_prediction_dict_rejects_inconsistent_score_lengths() -> None:
    with pytest.raises(ValueError, match="equal lengths"):
        video_file_ai._remap_prediction_dict(
            {"polyp": [0.2], "instrument": [0.4, 0.7]},
            {"polyp": ["polyp"], "instrument": ["instrument"]},
        )


@pytest.mark.django_db
@pytest.mark.parametrize("return_frame_scores", [True, False])
def test_predict_video_stream_returns_frame_scores_without_extracted_state(
    monkeypatch: MonkeyPatch,
    return_frame_scores: bool,
) -> None:
    center = Center.objects.create(name=f"stream-predict-center-{uuid.uuid4().hex[:8]}")
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash=f"stream-predict-video-{uuid.uuid4().hex}",
        fps=25.0,
        width=2,
        height=1,
    )
    state = video.get_or_create_state()
    assert state.frames_extracted is False

    label_a = Label.objects.create(name=f"stream-label-a-{uuid.uuid4().hex[:8]}")
    label_b = Label.objects.create(name=f"stream-label-b-{uuid.uuid4().hex[:8]}")
    label_set = LabelSet.objects.create(
        name=f"stream-labels-{uuid.uuid4().hex[:8]}",
        version=1,
    )
    label_set.labels.add(label_a, label_b)
    model_meta = ModelMeta.objects.create(
        name=f"stream-meta-{uuid.uuid4().hex[:8]}",
        version="1",
        model=AiModel.objects.create(name=f"stream-model-{uuid.uuid4().hex[:8]}"),
        labelset=label_set,
        batchsize=2,
        num_workers=0,
    )
    cast(_WritableFieldFile, model_meta.weights).save(
        f"stream-weights-{uuid.uuid4().hex}.pt",
        ContentFile(b"x" * 4096),
        save=True,
    )

    def fake_get_crop_template(self: VideoFile) -> list[int]:
        return [0, 1, 0, 2]

    monkeypatch.setattr(VideoFile, "get_crop_template", fake_get_crop_template)
    monkeypatch.setattr(
        "endoreg_db.utils.ai.MultiLabelClassificationNet",
        FakeMultilabelNet,
    )

    def fake_stream_predictions(
        *,
        video: VideoFile,
        model: object,
        classifier_config: object,
        crop_template: object,
        device: object,
        test_run: bool,
        n_test_frames: int,
        frame_source_file_type: str,
    ) -> tuple[list[list[float]], list[int], list[float], tuple[int, float] | None]:
        _ = model
        _ = crop_template
        _ = device
        _ = test_run
        _ = n_test_frames
        config_payload = AiPredictionConfigPayload.model_validate(
            cast(Any, classifier_config).model_dump(mode="python")
            if hasattr(classifier_config, "model_dump")
            else classifier_config
        )
        assert config_payload.labels == [label_a.name, label_b.name]
        assert video.pk == video.pk
        assert frame_source_file_type == "raw"
        return (
            [[0.1, 0.9], [0.8, 0.2]],
            [10, 11],
            [0.4, 0.44],
            (12, 0.53),
        )

    monkeypatch.setattr(
        video_file_ai,
        "_stream_predictions_from_video",
        fake_stream_predictions,
    )

    result = video.predict_video(
        model_meta=model_meta,
        return_frame_scores=return_frame_scores,
        smooth_window_size_s=0,
        frame_source_mode="stream",
    )

    if return_frame_scores:
        assert isinstance(result, VideoFrameScoreResult)
        assert result.labels == [label_a.name, label_b.name]
        assert result.frame_count == 2
        assert result.frame_numbers == [10, 11]
        assert result.timestamps == [0.4, 0.44]
        assert result.terminal_boundary == (12, 0.53)
    else:
        assert result == {label_a.name: [(11, 12)], label_b.name: [(10, 11)]}
    state.refresh_from_db()
    assert state.frames_extracted is False


@pytest.mark.parametrize("limit", [2, 3])
def test_stream_prediction_retains_only_observed_exclusive_boundary(
    monkeypatch: MonkeyPatch,
    limit: int,
) -> None:
    from endoreg_db.utils.frame_stream import FrameSample

    samples = [
        FrameSample(index, timestamp, np.zeros((2, 2, 3), dtype=np.uint8))
        for index, timestamp in enumerate((2.0, 2.1, 2.7))
    ]

    def frame_samples(*args: object, **kwargs: object) -> Iterator[FrameSample]:
        return iter(samples)

    def identity[T](value: T) -> T:
        return value

    monkeypatch.setattr(
        "endoreg_db.utils.frame_stream.iter_video_file_frame_samples",
        frame_samples,
    )
    config = AiPredictionConfigPayload(
        mean=(0.0, 0.0, 0.0),
        std=(1.0, 1.0, 1.0),
        size_x=2,
        size_y=2,
        batchsize=2,
        num_workers=0,
        activation=identity,
        labels=["finding"],
    )
    predictions, numbers, timestamps, boundary = (
        video_file_ai._stream_predictions_from_video(
            video=VideoFile(),
            model=torch.nn.Sequential(torch.nn.Flatten(), torch.nn.Linear(12, 1)),
            classifier_config=config,
            crop_template=(0, 2, 0, 2),
            device="cpu",
            test_run=True,
            n_test_frames=limit,
            frame_source_file_type="raw",
        )
    )
    assert len(predictions) == limit
    assert numbers == list(range(limit))
    assert timestamps == [sample.timestamp for sample in samples[:limit]]
    assert boundary == ((2, 2.7) if limit == 2 else None)


@pytest.mark.parametrize("boundary", [(1, 0.6), (2, 0.4), (2, float("nan"))])
def test_score_timeline_rejects_invalid_observed_terminal_boundary(
    boundary: tuple[int, float],
) -> None:
    from endoreg_db.services.video_files.temporal_inference import (
        TemporalInferenceConfigError,
        _resolve_score_timeline,
    )

    scores = VideoFrameScoreResult(
        labels=["finding"],
        frame_scores=np.ones((2, 1)),
        device="cpu",
        frame_count=2,
        frame_numbers=[0, 1],
        timestamps=[0.0, 0.4],
        terminal_boundary=boundary,
    )
    with pytest.raises(TemporalInferenceConfigError, match="terminal score boundary"):
        _resolve_score_timeline(VideoFile(), scores)

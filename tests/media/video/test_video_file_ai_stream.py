# pyright: reportPrivateUsage=false
from __future__ import annotations

import uuid
from typing import Any, Protocol, cast

import pytest
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
def test_predict_video_stream_returns_frame_scores_without_extracted_state(
    monkeypatch: MonkeyPatch,
) -> None:
    center = Center.objects.create(name=f"stream-predict-center-{uuid.uuid4().hex[:8]}")
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"stream-predict-video-{uuid.uuid4().hex}",
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
    ) -> tuple[list[list[float]], list[int], list[float]]:
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
        )

    monkeypatch.setattr(
        video_file_ai,
        "_stream_predictions_from_video",
        fake_stream_predictions,
    )

    result = video.predict_video(
        model_meta=model_meta,
        return_frame_scores=True,
        frame_source_mode="stream",
    )

    assert isinstance(result, VideoFrameScoreResult)
    assert result.labels == [label_a.name, label_b.name]
    assert result.frame_count == 2
    assert result.frame_numbers == [10, 11]
    assert result.timestamps == [0.4, 0.44]
    state.refresh_from_db()
    assert state.frames_extracted is False

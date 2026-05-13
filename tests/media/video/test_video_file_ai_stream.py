from __future__ import annotations

import uuid

import pytest
from django.core.files.base import ContentFile

from endoreg_db.models import AiModel, Center, Label, LabelSet, ModelMeta, VideoFile
from endoreg_db.models.media.video import video_file_ai
from endoreg_db.models.media.video.video_file_ai import VideoFrameScoreResult


class FakeModel:
    def to(self, device):
        return self

    def eval(self):
        return self


class FakeMultilabelNet:
    @classmethod
    def load_from_checkpoint(cls, *args, **kwargs):
        return FakeModel()


@pytest.mark.django_db
def test_predict_video_stream_returns_frame_scores_without_extracted_state(
    monkeypatch,
):
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
    model_meta.weights.save(
        f"stream-weights-{uuid.uuid4().hex}.pt",
        ContentFile(b"x" * 4096),
        save=True,
    )

    monkeypatch.setattr(VideoFile, "get_crop_template", lambda self: [0, 1, 0, 2])
    monkeypatch.setattr(
        "endoreg_db.utils.ai.MultiLabelClassificationNet",
        FakeMultilabelNet,
    )

    def fake_stream_predictions(**kwargs):
        assert kwargs["video"].pk == video.pk
        assert kwargs["frame_source_file_type"] == "raw"
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

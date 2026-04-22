import pytest

import endoreg_db.models.media.video.video_file_segments as segments_module
from endoreg_db.models import (
    AiModel,
    Center,
    Frame,
    Label,
    LabelSet,
    LabelType,
    LabelVideoSegment,
    ModelMeta,
    VideoFile,
    VideoPredictionMeta,
)


@pytest.mark.django_db
def test_convert_sequences_creates_segments():
    center = Center.objects.create(
        name="segments-center", display_name="Segments Center"
    )
    video = VideoFile.objects.create(center=center, video_hash="segments-hash")

    label_type = LabelType.objects.create(name="video")
    label = Label.objects.create(name="lesion", label_type=label_type)

    labelset = LabelSet.objects.create(name="set-a", version=1)
    labelset.labels.add(label)

    ai_model = AiModel.objects.create(name="model-a")
    model_meta = ModelMeta.objects.create(
        name="meta-a", version="1", model=ai_model, labelset=labelset
    )
    prediction_meta = VideoPredictionMeta.objects.create(
        model_meta=model_meta, video_file=video
    )

    sequences = {
        "lesion": [(0, 4), (10, 12)],
        "unknown": [(3, 6)],
    }

    segments_module._convert_sequences_to_db_segments(video, sequences, prediction_meta)

    created = LabelVideoSegment.objects.filter(
        video_file=video, label=label, prediction_meta=prediction_meta
    )
    assert created.count() == 2
    assert all(segment.state is not None for segment in created)
    assert {segment.source.name for segment in created} == {"prediction"}


@pytest.mark.django_db
def test_convert_sequences_skips_single_frame_segments():
    center = Center.objects.create(
        name="singleton-center", display_name="Singleton Center"
    )
    video = VideoFile.objects.create(center=center, video_hash="singleton-hash")

    label_type = LabelType.objects.create(name="video")
    label = Label.objects.create(name="appendix", label_type=label_type)

    labelset = LabelSet.objects.create(name="set-b", version=1)
    labelset.labels.add(label)

    ai_model = AiModel.objects.create(name="model-b")
    model_meta = ModelMeta.objects.create(
        name="meta-b", version="1", model=ai_model, labelset=labelset
    )
    prediction_meta = VideoPredictionMeta.objects.create(
        model_meta=model_meta, video_file=video
    )

    sequences = {
        "appendix": [(5, 5), (10, 12)],
    }

    segments_module._convert_sequences_to_db_segments(video, sequences, prediction_meta)

    created = LabelVideoSegment.objects.filter(
        video_file=video, label=label, prediction_meta=prediction_meta
    )
    assert created.count() == 1
    segment = created.get()
    assert segment.start_frame_number == 10
    assert segment.end_frame_number == 12
    assert segment.source.name == "prediction"
    assert segment.state is not None


@pytest.mark.django_db
def test_get_outside_helpers_return_expected_frames(tmp_path):
    center = Center.objects.create(name="outside-center", display_name="Outside Center")
    video = VideoFile.objects.create(center=center, video_hash="outside-hash")
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    video.frame_dir = str(frame_dir)

    label_type = LabelType.objects.create(name="video")
    outside_label = Label.objects.create(name="outside", label_type=label_type)

    LabelVideoSegment.objects.create(
        video_file=video, label=outside_label, start_frame_number=1, end_frame_number=3
    )
    LabelVideoSegment.objects.create(
        video_file=video, label=outside_label, start_frame_number=5, end_frame_number=6
    )

    Frame.objects.create(video=video, frame_number=0, relative_path="frame_0.jpg")
    Frame.objects.create(video=video, frame_number=1, relative_path="frame_1.jpg")
    Frame.objects.create(video=video, frame_number=2, relative_path="frame_2.jpg")
    Frame.objects.create(video=video, frame_number=5, relative_path="frame_5.jpg")

    numbers = segments_module._get_outside_frame_numbers(video)
    assert numbers == {1, 2, 3, 5, 6}

    frames_qs = segments_module._get_outside_frames(video)
    assert list(frames_qs.values_list("frame_number", flat=True)) == [1, 2, 5]

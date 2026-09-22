from __future__ import annotations

import pytest

from endoreg_db.helpers.typing import m2m_add_relation

from endoreg_db.models import (
    AIDataSet,
    Center,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    InformationSourceType,
    Label,
    LabelVideoSegment,
    LabelVideoSegmentState,
    VideoFile,
)
from endoreg_db.services.aidataset_training_selection import (
    training_annotation_querysets,
)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("source_name", "source_types", "validated", "frame_allowed", "segment_allowed"),
    [
        ("manual_annotation", (), False, True, True),
        ("review_team", ("manual_annotation", "annotation"), False, True, True),
        ("prediction", (), False, False, False),
        ("prediction", (), True, False, True),
        (None, (), False, False, False),
        (None, (), True, False, True),
        ("unknown", (), False, False, False),
        ("manual_annotation", ("annotation", "prediction"), False, False, False),
        ("manual_annotation", ("annotation", "prediction"), True, False, True),
        ("prediction_legacy", ("annotation",), False, False, False),
        ("model_legacy", ("annotation",), False, False, False),
    ],
)
def test_training_sources_require_manual_origin_or_segment_confirmation(
    source_name: str | None,
    source_types: tuple[str, ...],
    validated: bool,
    frame_allowed: bool,
    segment_allowed: bool,
) -> None:
    # Arrange: dataset-bound labels with explicit provenance, including ambiguous sources.
    source = InformationSource.objects.create(name=source_name) if source_name else None
    if source is not None:
        for source_type in source_types:
            m2m_add_relation(getattr(source, "information_source_types")).add(
                InformationSourceType.objects.get_or_create(name=source_type)[0]
            )
    video = VideoFile.objects.create(
        center=Center.objects.create(name="selection-center"),
        raw_video_hash="selection-video",
        fps=25.0,
        frame_count=2,
    )
    frame = Frame.objects.create(video=video, frame_number=0)
    label = Label.objects.create(name="selection-label")
    annotation = ImageClassificationAnnotation.objects.create(
        frame=frame,
        label=label,
        value=True,
        information_source=source,
    )
    segment = LabelVideoSegment.objects.create(
        video_file=video,
        label=label,
        source=source,
        start_frame_number=0,
        end_frame_number=1,
    )
    LabelVideoSegmentState.objects.update_or_create(
        origin=segment,
        defaults={"is_validated": validated},
    )
    dataset = AIDataSet.objects.create(
        name="selection-dataset",
        dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
        ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
    )
    dataset.image_annotations.add(annotation)
    dataset.video_annotations.add(segment)

    # Act.
    annotations, segments = training_annotation_querysets(dataset, source_scope="all")

    # Assert: multiple source types must neither duplicate nor launder predictions.
    assert list(annotations.values_list("pk", flat=True)) == (
        [annotation.pk] if frame_allowed else []
    )
    assert list(segments.values_list("pk", flat=True)) == (
        [segment.pk] if segment_allowed else []
    )

from __future__ import annotations

from uuid import uuid4

import pytest
from django.test.utils import override_settings
from django.utils import timezone
from pydantic import ValidationError as PydanticValidationError

from endoreg_db.models import (
    AIDataSet,
    Center,
    Frame,
    ImageClassificationAnnotation,
    Label,
    LabelVideoSegment,
    VideoFile,
    VideoState,
)
from endoreg_db.services.media.export_policy import validated_export_video_filter
from endoreg_db.services.datasets.exports import (
    AIDataSetExportPayload,
    build_export_payload,
    export_to_standardized_structure,
)


def _dataset() -> AIDataSet:
    return AIDataSet.objects.create(
        name=f"dataset-scope-{uuid4().hex[:8]}",
        dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
        ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
    )


@pytest.mark.django_db
def test_dataset_export_rejects_conflicting_scope_at_service_boundary():
    dataset = _dataset()
    center = Center.objects.create(name=f"scope-center-{uuid4().hex[:8]}")

    with pytest.raises(ValueError, match="center_key or all_centers"):
        export_to_standardized_structure(
            dataset,
            center_key=center.center_key,
            all_centers=True,
            only_validated=True,
        )


@pytest.mark.django_db
def test_dataset_export_rejects_unscoped_local_study_server_export():
    dataset = _dataset()

    with override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server"):
        with pytest.raises(ValueError, match="exactly one center scope"):
            export_to_standardized_structure(dataset, only_validated=True)


@pytest.mark.django_db
def test_dataset_export_rejects_unvalidated_local_study_server_export():
    dataset = _dataset()
    center = Center.objects.create(name=f"scope-center-{uuid4().hex[:8]}")

    with override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server"):
        with pytest.raises(ValueError, match="only_validated=true"):
            export_to_standardized_structure(
                dataset,
                center_key=center.center_key,
                only_validated=False,
            )


@pytest.mark.django_db
def test_dataset_export_contract_rejects_unknown_fields():
    payload = build_export_payload(_dataset())
    untrusted_payload = {
        **payload.model_dump(mode="python"),
        "protected_internal_note": "must not pass the export boundary",
    }

    with pytest.raises(PydanticValidationError, match="Extra inputs are not permitted"):
        AIDataSetExportPayload.model_validate(untrusted_payload)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("anonymized", "outside_removed", "ready", "approval_hash", "local", "eligible"),
    [
        (True, True, True, "a" * 64, True, True),
        (False, True, False, "a" * 64, True, False),
        (True, False, False, "a" * 64, True, False),
        (True, True, False, "a" * 64, True, False),
        (True, True, False, "", True, False),
        (True, False, False, "", False, True),
        (False, False, False, "", False, False),
    ],
)
def test_shared_export_approval_filter_for_frame_and_segment_relations(
    anonymized: bool,
    outside_removed: bool,
    ready: bool,
    approval_hash: str,
    local: bool,
    eligible: bool,
) -> None:
    suffix = uuid4().hex
    state = VideoState.objects.create()
    video = VideoFile.objects.create(
        center=Center.objects.create(name=f"export-filter-{suffix}"),
        raw_video_hash=suffix,
        state=state,
    )
    frame = Frame.objects.create(video=video, frame_number=0)
    label = Label.objects.create(name=f"export-filter-{suffix}")
    annotation = ImageClassificationAnnotation.objects.create(
        frame=frame, label=label, value=True
    )
    segment = LabelVideoSegment.objects.create(
        video_file=video, label=label, start_frame_number=0, end_frame_number=1
    )
    # Segment creation invalidates approval; establish the state under test afterward.
    VideoState.objects.filter(pk=state.pk).update(
        anonymization_validated=anonymized,
        outside_segments_removed=outside_removed,
        segment_annotations_validated=ready,
        ready_for_export=ready,
        ready_for_export_by="export-filter-reviewer" if ready else "",
        ready_for_export_at=timezone.now() if ready else None,
        processed_file_sha256=approval_hash,
    )
    # Metadata selection must not require materialized media on this node.
    assert not video.processed_file
    assert (
        ImageClassificationAnnotation.objects.filter(
            validated_export_video_filter(
                video_prefix="frame__video", require_export_approval=local
            ),
            pk=annotation.pk,
        ).exists()
        is eligible
    )
    assert (
        LabelVideoSegment.objects.filter(
            validated_export_video_filter(
                video_prefix="video_file", require_export_approval=local
            ),
            pk=segment.pk,
        ).exists()
        is eligible
    )

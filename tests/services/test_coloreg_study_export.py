from __future__ import annotations

# pyright: reportUnknownMemberType=false

from typing import cast
from uuid import uuid4

import pytest
from django.core.files.base import ContentFile

from endoreg_db.models import (
    AIDataSet,
    Frame,
    ImageClassificationAnnotation,
    Label,
    LabelSet,
    Patient,
    PatientExamination,
    VideoFile,
)
from endoreg_db.services.aidataset_exports import export_coloreg_study_dataset
from lx_dtypes.models.contracts.json_types import JsonObject


def _video(*, patient_examination: PatientExamination | None) -> VideoFile:
    from tests.helpers.default_objects import get_default_center

    suffix = uuid4().hex[:8]
    video = VideoFile.objects.create(
        center=get_default_center(),
        patient=(
            patient_examination.patient if patient_examination is not None else None
        ),
        examination=patient_examination,
        video_hash=f"coloreg-{suffix}",
        original_file_name=f"coloreg-{suffix}.mp4",
    )
    video.processed_file.save(
        f"coloreg-{suffix}_processed.mp4",
        ContentFile(b"processed-coloreg-video"),
        save=True,
    )
    return video


def _labels() -> tuple[Label, Label, Label]:
    cold_snare, _ = Label.objects.get_or_create(
        name="cold_snare",
        defaults={"label_type": "classification"},
    )
    hot_snare, _ = Label.objects.get_or_create(
        name="hot_snare",
        defaults={"label_type": "classification"},
    )
    generic_snare, _ = Label.objects.get_or_create(
        name="snare",
        defaults={"label_type": "classification"},
    )
    labelset, _ = LabelSet.objects.get_or_create(
        name="coloreg_export_test",
        version=1,
    )
    labelset.labels.add(cold_snare, hot_snare, generic_snare)
    return cold_snare, hot_snare, generic_snare


@pytest.mark.django_db
def test_coloreg_export_projects_segment_and_frame_labels_to_study_findings(
    base_db_data: object,
) -> None:
    del base_db_data
    patient = Patient.objects.create(patient_hash=f"coloreg-patient-{uuid4().hex[:8]}")
    patient_examination = PatientExamination.objects.create(patient=patient)
    video = _video(patient_examination=patient_examination)
    cold_snare, hot_snare, generic_snare = _labels()

    cold_segment = video.label_video_segments.create(
        label=cold_snare,
        start_frame_number=10,
        end_frame_number=20,
    )
    ignored_segment = video.label_video_segments.create(
        label=generic_snare,
        start_frame_number=30,
        end_frame_number=40,
    )
    frame = Frame.objects.create(
        video=video,
        frame_number=15,
        relative_path="frame_0000015.jpg",
        is_extracted=True,
    )
    hot_annotation = ImageClassificationAnnotation.objects.create(
        frame=frame,
        label=hot_snare,
        value=True,
        annotator="coloreg-test",
    )
    dataset = AIDataSet.objects.create(
        name="coloreg-study-export",
        dataset_type=AIDataSet.DATASET_TYPE_VIDEO,
        ai_model_type=AIDataSet.AI_MODEL_TYPE_VIDEO_SEGMENT_CLASSIFICATION,
    )
    dataset.video_annotations.add(cold_segment, ignored_segment)
    dataset.image_annotations.add(hot_annotation)

    payload = export_coloreg_study_dataset(dataset)

    assert payload["schema_version"] == "coloreg_study_dataset_v1"
    assert payload["knowledge_base_module"] == "coloreg"
    assert payload["knowledge_base_version"] == "0.1.0"
    studies = cast(list[JsonObject], payload["studies"])
    assert len(studies) == 1
    assert studies[0]["patient_examination_id"] == patient_examination.pk
    findings = cast(list[JsonObject], studies[0]["findings"])
    assert len(findings) == 1
    assert findings[0]["finding_name"] == "colon_polyp"
    assert findings[0]["intervention_names"] == [
        "endoscopy_cold_snare_resection_generic",
        "endoscopy_diathermic_snare_resection_generic",
    ]
    evidence = cast(list[JsonObject], findings[0]["evidence"])
    assert {str(item["modality"]) for item in evidence} == {"segment", "frame"}
    segment_evidence = next(item for item in evidence if item["modality"] == "segment")
    assert segment_evidence["segment_id"] == cold_segment.pk
    assert segment_evidence["start_frame_number"] == 10
    assert segment_evidence["end_frame_number"] == 20
    frame_evidence = next(item for item in evidence if item["modality"] == "frame")
    assert frame_evidence["annotation_id"] == hot_annotation.pk
    assert frame_evidence["frame_number"] == 15


@pytest.mark.django_db
def test_coloreg_export_rejects_mapped_video_without_study(
    base_db_data: object,
) -> None:
    del base_db_data
    video = _video(patient_examination=None)
    cold_snare, _hot_snare, _generic_snare = _labels()
    segment = video.label_video_segments.create(
        label=cold_snare,
        start_frame_number=1,
        end_frame_number=2,
    )
    dataset = AIDataSet.objects.create(
        name="coloreg-missing-study",
        dataset_type=AIDataSet.DATASET_TYPE_VIDEO,
        ai_model_type=AIDataSet.AI_MODEL_TYPE_VIDEO_SEGMENT_CLASSIFICATION,
    )
    dataset.video_annotations.add(segment)

    with pytest.raises(ValueError, match="has no patient examination"):
        export_coloreg_study_dataset(dataset)

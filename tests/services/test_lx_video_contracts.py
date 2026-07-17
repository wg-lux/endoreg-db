# pyright: reportUnknownMemberType=false

import json
from datetime import date, datetime, time
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from endoreg_db.models import (
    AIDataSet,
    Gender,
    Label,
    LabelSet,
    ModelMeta,
    Patient,
    PatientExamination,
    PatientExternalID,
    SensitiveMeta,
    VideoFile,
    VideoPredictionMeta,
    VideoState,
    Center,
)
from endoreg_db.services.lx_video_contracts import (
    build_lx_p_video_segment,
    build_lx_patient_video_file,
    build_lx_sensitive_meta,
    resolve_lx_anonymization_state,
    resolve_segment_labelset_name,
)


def _create_video(
    *,
    center: Center,
    video_hash: str,
    patient: Patient | None = None,
    patient_examination: PatientExamination | None = None,
    sensitive_meta: SensitiveMeta | None = None,
    state: VideoState | None = None,
) -> VideoFile:
    video = VideoFile.objects.create(
        center=center,
        video_hash=video_hash,
        patient=patient,
        examination=patient_examination,
        sensitive_meta=sensitive_meta,
        state=state,
        original_file_name=f"{video_hash}.mp4",
    )
    video.processed_file.save(
        f"{video_hash}_processed.mp4",
        ContentFile(b"processed-video-bytes"),
        save=True,
    )
    return video


@pytest.mark.django_db
def test_build_lx_sensitive_meta_maps_django_fields(
    base_db_data: object,
) -> None:
    from tests.helpers.default_objects import get_default_center

    center = get_default_center()
    gender = Gender.objects.get(name="female")
    patient = Patient.objects.create(
        first_name="Pseudo",
        last_name="Patient",
        dob=date(1980, 1, 1),
        gender=gender,
        center=center,
    )
    pseudo_examination = PatientExamination.objects.create(patient=patient)
    external_id = PatientExternalID.objects.create(
        patient=patient,
        origin="hospital",
        external_id="ext-42",
    )
    sensitive_meta = SensitiveMeta.objects.create(
        examination_date=date(2024, 5, 17),
        examination_time=time(9, 30),
        casenumber="case-1",
        pseudo_patient=patient,
        pseudo_examination=pseudo_examination,
        patient_gender=gender,
        center=center,
        patient_first_name="Alice",
        patient_last_name="Miller",
        patient_dob=timezone.make_aware(datetime(1980, 1, 1, 0, 0)),
        endoscope_type="scope",
        endoscope_sn="sn-1",
        text="raw text",
        anonymized_text="anon text",
        external_id=external_id,
        file_path="protected/sensitive/source.mp4",
    )
    sensitive_meta.state_safe.dob_verified = True
    sensitive_meta.state_safe.names_verified = True
    sensitive_meta.state_safe.save(update_fields=["dob_verified", "names_verified"])

    lx_sensitive_meta = build_lx_sensitive_meta(sensitive_meta)

    assert lx_sensitive_meta is not None
    assert lx_sensitive_meta.first_name == "Alice"
    assert lx_sensitive_meta.last_name == "Miller"
    assert lx_sensitive_meta.gender == "female"
    assert lx_sensitive_meta.external_id == "ext-42"
    assert lx_sensitive_meta.center == center.name
    assert lx_sensitive_meta.file_path == "protected/sensitive/source.mp4"
    assert str(lx_sensitive_meta.uuid) == str(
        uuid5(NAMESPACE_URL, f"urn:endoreg-db:sensitive-meta:{sensitive_meta.pk}")
    )
    assert lx_sensitive_meta.state.dob_verified is True
    assert lx_sensitive_meta.state.name_verified is True
    assert lx_sensitive_meta.pseudo_patient == str(sensitive_meta.pseudo_patient_id)
    assert lx_sensitive_meta.pseudo_examination == str(
        sensitive_meta.pseudo_examination_id
    )
    assert lx_sensitive_meta.pseudo_examiners == [
        str(examiner.pk) for examiner in sensitive_meta.examiners.all()
    ]


@pytest.mark.django_db
def test_build_lx_p_video_segment_prefers_prediction_meta_labelset(
    base_db_data: object,
    unique_ai_model: Any,
    base_labelset: LabelSet,
) -> None:
    from tests.helpers.default_objects import get_default_center

    center = get_default_center()
    label = Label.objects.create(name="lesion")
    base_labelset.labels.add(label)
    model_meta = ModelMeta.objects.create(
        name="typed-seg",
        version="1",
        model=unique_ai_model,
        labelset=base_labelset,
    )
    video = _create_video(center=center, video_hash="pred-hash")
    prediction_meta = VideoPredictionMeta.objects.create(
        model_meta=model_meta,
        video_file=video,
    )
    segment = video.label_video_segments.create(
        label=label,
        prediction_meta=prediction_meta,
        start_frame_number=10,
        end_frame_number=20,
    )

    lx_segment = build_lx_p_video_segment(segment)

    assert lx_segment.label == "lesion"
    assert lx_segment.labelset == base_labelset.name
    assert lx_segment.patient_video_file == str(video.uuid)
    assert str(lx_segment.uuid) == str(
        uuid5(NAMESPACE_URL, f"urn:endoreg-db:label-video-segment:{segment.pk}")
    )
    assert lx_segment.state.prediction is True
    assert lx_segment.state.annotation is False


@pytest.mark.django_db
def test_build_lx_patient_video_file_rejects_invalid_segments_without_omission(
    base_db_data: object,
) -> None:
    from tests.helpers.default_objects import get_default_center

    center = get_default_center()
    gender = Gender.objects.get(name="female")
    patient = Patient.objects.create(
        first_name="Valid",
        last_name="Patient",
        dob=date(1975, 6, 1),
        gender=gender,
        center=center,
    )
    patient_examination = PatientExamination.objects.create(patient=patient)
    sensitive_meta = SensitiveMeta.objects.create(
        pseudo_patient=patient,
        pseudo_examination=patient_examination,
        patient_gender=gender,
        center=center,
        patient_first_name="Valid",
        patient_last_name="Patient",
    )
    state = VideoState.objects.create(anonymization_validated=True)
    video = _create_video(
        center=center,
        video_hash="video-contract",
        patient=patient,
        patient_examination=patient_examination,
        sensitive_meta=sensitive_meta,
        state=state,
    )
    label = Label.objects.create(name="outside")
    labelset = LabelSet.objects.create(name="segments_default", version=1)
    labelset.labels.add(label)
    valid_segment = video.label_video_segments.create(
        label=label,
        start_frame_number=1,
        end_frame_number=5,
        export_segment=True,
    )
    video.label_video_segments.create(
        label=None,
        start_frame_number=8,
        end_frame_number=12,
    )

    with pytest.raises(ValueError, match="cannot be exported"):
        build_lx_patient_video_file(video)

    invalid_segment = video.label_video_segments.get(label__isnull=True)
    invalid_segment.delete()
    lx_video = build_lx_patient_video_file(video)

    assert resolve_lx_anonymization_state(video).value == "validated"
    assert lx_video.anonymization_state.value == "validated"
    assert lx_video.patient == str(patient.pk)
    assert lx_video.patient_examination == str(patient_examination.pk)
    sensitive_meta = lx_video.sensitive_meta
    assert sensitive_meta is not None
    assert sensitive_meta.first_name == "Valid"
    assert len(lx_video.patient_video_segments) == 1
    only_segment = next(iter(lx_video.patient_video_segments.values()))
    assert valid_segment.label is not None
    assert only_segment.label == valid_segment.label.name
    assert only_segment.labelset == labelset.name
    assert only_segment.state.annotation is True
    assert only_segment.state.prediction is False


@pytest.mark.django_db
def test_build_lx_patient_video_file_invalid_segment_raises_by_default(
    base_db_data: object,
) -> None:
    from tests.helpers.default_objects import get_default_center

    center = get_default_center()
    video = _create_video(center=center, video_hash="strict-hash")
    video.label_video_segments.create(
        label=None,
        start_frame_number=4,
        end_frame_number=9,
    )

    with pytest.raises(ValueError, match="cannot be exported"):
        build_lx_patient_video_file(video)


@pytest.mark.django_db
def test_build_lx_sensitive_meta_rejects_contradictory_dates(
    base_db_data: object,
) -> None:
    from tests.helpers.default_objects import get_default_center

    center = get_default_center()
    sensitive_meta = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="Date",
        patient_last_name="Conflict",
        patient_dob=timezone.make_aware(datetime(2025, 1, 1, 0, 0)),
        examination_date=date(2024, 1, 1),
    )

    with pytest.raises(ValueError, match="examination_date before dob"):
        build_lx_sensitive_meta(sensitive_meta)


@pytest.mark.django_db
def test_ai_dataset_json_export_omits_sensitive_meta(
    base_db_data: object,
) -> None:
    from tests.helpers.default_objects import get_default_center

    center = get_default_center()
    gender = Gender.objects.get(name="female")
    patient = Patient.objects.create(
        first_name="Pseudonym",
        last_name="ForExport",
        dob=date(1980, 1, 1),
        gender=gender,
        center=center,
    )
    sensitive_meta = SensitiveMeta.objects.create(
        center=center,
        patient_gender=gender,
        pseudo_patient=patient,
        patient_first_name="ProtectedAlice",
        patient_last_name="ProtectedMiller",
        patient_dob=timezone.make_aware(datetime(1980, 1, 1, 0, 0)),
        casenumber="protected-case-42",
        external_id=PatientExternalID.objects.create(
            patient=patient,
            origin="hospital",
            external_id="protected-external-id",
        ),
        text="protected raw report text",
    )
    video = _create_video(
        center=center,
        video_hash="safe-serialization-video",
        sensitive_meta=sensitive_meta,
    )
    label = Label.objects.create(name="safe-export-label")
    labelset = LabelSet.objects.create(name="safe_export_labelset", version=1)
    labelset.labels.add(label)
    segment = video.label_video_segments.create(
        label=label,
        start_frame_number=1,
        end_frame_number=4,
    )
    dataset = AIDataSet.objects.create(
        name="safe-serialization-dataset",
        dataset_type=AIDataSet.DATASET_TYPE_VIDEO,
        ai_model_type=AIDataSet.AI_MODEL_TYPE_VIDEO_SEGMENT_CLASSIFICATION,
    )
    dataset.video_annotations.add(segment)

    payload = dataset.export_to_standardized_structure()

    patient_videos = payload["patient_videos"]
    assert isinstance(patient_videos, dict)
    exported_video = cast(dict[str, object], patient_videos[str(video.uuid)])
    assert isinstance(exported_video, dict)
    assert "sensitive_meta" not in exported_video
    serialized = json.dumps(payload, sort_keys=True)
    for protected_value in (
        "ProtectedAlice",
        "ProtectedMiller",
        "1980-01-01",
        "protected-case-42",
        "protected-external-id",
        "protected raw report text",
    ):
        assert protected_value not in serialized


@pytest.mark.django_db
def test_resolve_segment_labelset_name_falls_back_to_label_membership(
    base_db_data: object,
) -> None:
    from tests.helpers.default_objects import get_default_center

    center = get_default_center()
    video = _create_video(center=center, video_hash="fallback-labelset")
    label = Label.objects.create(name="fallback")
    labelset = LabelSet.objects.create(name="fallback_set", version=2)
    labelset.labels.add(label)
    segment = video.label_video_segments.create(
        label=label,
        start_frame_number=2,
        end_frame_number=6,
    )

    assert resolve_segment_labelset_name(segment) == "fallback_set"
    assert segment.resolve_labelset_name() == "fallback_set"


@pytest.mark.django_db
def test_segment_resolve_labelset_name_falls_back_to_video_model_meta(
    base_db_data: object,
    unique_ai_model: Any,
) -> None:
    from tests.helpers.default_objects import get_default_center

    center = get_default_center()
    labelset = LabelSet.objects.create(name="video_level_set", version=3)
    model_meta = ModelMeta.objects.create(
        name="video-level-model",
        version="1",
        model=unique_ai_model,
        labelset=labelset,
    )
    video = _create_video(center=center, video_hash="video-level-labelset")
    video.ai_model_meta = model_meta
    video.save(update_fields=["ai_model_meta"])
    segment = video.label_video_segments.create(
        label=None,
        start_frame_number=3,
        end_frame_number=9,
    )

    assert segment.resolve_labelset() == labelset
    assert segment.resolve_labelset_name() == "video_level_set"

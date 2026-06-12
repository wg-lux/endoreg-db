# pyright: reportUnknownMemberType=false

from datetime import date, datetime, time
from typing import Any

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from endoreg_db.models import (
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
    Center
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
def test_build_lx_sensitive_meta_maps_django_fields(base_db_data: object,) -> None:
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
    )

    lx_sensitive_meta = build_lx_sensitive_meta(sensitive_meta)

    assert lx_sensitive_meta is not None
    assert lx_sensitive_meta.first_name == "Alice"
    assert lx_sensitive_meta.last_name == "Miller"
    assert lx_sensitive_meta.gender == "female"
    assert lx_sensitive_meta.external_id == "ext-42"
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


@pytest.mark.django_db
def test_build_lx_patient_video_file_skips_invalid_segments(base_db_data: object,) -> None:
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


@pytest.mark.django_db
def test_build_lx_patient_video_file_strict_segments_raises(base_db_data: object,) -> None:
    from tests.helpers.default_objects import get_default_center

    center = get_default_center()
    video = _create_video(center=center, video_hash="strict-hash")
    video.label_video_segments.create(
        label=None,
        start_frame_number=4,
        end_frame_number=9,
    )

    with pytest.raises(ValueError, match="cannot be exported"):
        build_lx_patient_video_file(video, strict_segments=True)


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

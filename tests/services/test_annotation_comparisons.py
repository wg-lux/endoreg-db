from __future__ import annotations

from math import isclose
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIRequestFactory, force_authenticate
from lx_dtypes.models.contracts.finding_review import AnnotationFindingMappings

from endoreg_db.models import (
    Center,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    Patient,
    PatientExamination,
    VideoFile,
    AiModel,
    LabelSet,
    ModelMeta,
    VideoPredictionMeta,
    LabelVideoSegment,
)
from endoreg_db.models.report.patient_examination_report import PatientExaminationReport
from endoreg_db.services.annotations.comparisons import (
    compare_annotations,
    compare_findings,
    video_comparisons,
)
from endoreg_db.services.annotations.segment_annotations import (
    ensure_segment_annotations,
)
from endoreg_db.views.media.annotation_comparisons import AnnotationComparisonsView


@pytest.mark.parametrize(
    "other, expected", [({(0, "polyp"): True}, 25), ({(1, "polyp"): True}, 75), ({}, 0)]
)
def test_overlap_weights_persisted_duration(
    other: dict[tuple[int, str], bool], expected: float
) -> None:
    result = compare_annotations(
        "peer", {(0, "polyp"): True, (1, "polyp"): True}, other, {0: 0.1, 1: 0.3}
    )
    assert result.overlap is not None and isclose(result.overlap, expected)
    assert result.union is not None and isclose(result.union, 0.4)


def test_missing_timing_and_unreviewed_labels_are_not_disagreement() -> None:
    result = compare_annotations(
        "peer", {(0, "polyp"): True}, {(1, "polyp"): True}, {0: 0.1}
    )
    assert result.missing == 1
    assert result.overlap is None
    assert result.intersection is None
    assert result.agreement is None
    assert result.shared == 0


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf")])
def test_invalid_duration_rejected(duration: float) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        compare_annotations("peer", {}, {}, {0: duration})


def test_negative_observations_and_empty_findings() -> None:
    result = compare_annotations("peer", {(0, "p"): False}, {(0, "p"): False}, {0: 0.1})
    assert result.agreement == 100
    assert result.overlap is None
    assert compare_findings("peer", "report", set(), set()).overlap is None
    findings = compare_findings("peer", "report", {"p", "a"}, {"p", "b"})
    assert findings.matching == ["p"]
    assert findings.own == ["a"]
    assert findings.other == ["b"]
    assert findings.overlap is not None and isclose(findings.overlap, 100 / 3)


@pytest.fixture
def annotated_video(db: None) -> VideoFile:
    center = Center.objects.create(name="comparison-center")
    patient = Patient.objects.create(patient_hash="comparison-patient", center=center)
    examination = PatientExamination.objects.create(
        patient=patient, knowledge_base_module="coloreg", knowledge_base_version="1.0"
    )
    video = VideoFile.objects.create(
        center=center,
        examination=examination,
        raw_video_hash="comparison-video",
        frame_count=3,
        fps=25,
    )
    label = Label.objects.create(name="polyp")
    source = InformationSource.objects.create(name="manual_annotation")
    duplicate_source = InformationSource.objects.create(name="human_annotation")
    prediction_source = InformationSource.objects.create(name="prediction")
    for number, timestamp in enumerate([0.0, 0.1, 0.4]):
        frame = Frame.objects.create(
            video=video, frame_number=number, timestamp=timestamp
        )
        if number == 2:
            continue
        for author, annotation_source in [
            ("me", source),
            ("me", duplicate_source),
            ("peer", source),
            ("robot", prediction_source),
            (None, source),
        ]:
            ImageClassificationAnnotation.objects.create(
                frame=frame,
                label=label,
                annotator=author,
                information_source=annotation_source,
                value=author != "peer" or number == 0,
            )
    return video


def mapping() -> AnnotationFindingMappings:
    return AnnotationFindingMappings.model_validate(
        {
            "schema_version": "coloreg_annotation_mapping_v1",
            "knowledge_base_module": "coloreg",
            "knowledge_base_version": "1.0",
            "labelset_names": [],
            "mappings": [
                {
                    "label": "polyp",
                    "modalities": ["frame", "segment"],
                    "finding_name": "colon_polyp",
                }
            ],
        }
    )


@pytest.mark.django_db
def test_service_preserves_tracks_and_uses_only_final_snapshots(
    annotated_video: VideoFile,
) -> None:
    peer = User.objects.create_user(username="peer")
    report = PatientExaminationReport.objects.create(
        patient_examination=annotated_video.examination,
        template_name="test",
        status="final",
        finalized_by=peer,
        dtypes_record={
            "patient": "1",
            "examination": "colonoscopy",
            "knowledge_base_module": "coloreg",
            "knowledge_base_version": "1.0",
            "patient_findings": [
                {
                    "finding": "colon_polyp",
                    "patient_examination": "colonoscopy",
                }
            ],
        },
    )
    PatientExaminationReport.objects.create(
        patient_examination=annotated_video.examination,
        template_name="draft",
        status="draft",
        finalized_by=peer,
    )
    before = list(
        ImageClassificationAnnotation.objects.values_list("pk", "value", "annotator")
    )
    with patch(
        "endoreg_db.services.annotations.comparisons._mapping", return_value=mapping()
    ):
        result = video_comparisons(
            annotated_video,
            principal="me",
            reports=PatientExaminationReport.objects.all(),
        )
    assert [row.practitioner for row in result.annotations] == ["peer"]
    assert result.annotations[0].shared == 2
    assert result.annotations[0].agreement == 50
    assert result.annotations[0].overlap is not None and isclose(
        result.annotations[0].overlap, 25
    )
    assert [(row.kind, row.report) for row in result.findings] == [
        ("annotations", None),
        ("report", report.pk),
    ]
    assert all(row.matching == ["colon_polyp"] for row in result.findings)
    assert (
        list(
            ImageClassificationAnnotation.objects.values_list(
                "pk", "value", "annotator"
            )
        )
        == before
    )


@pytest.mark.django_db
def test_conflicts_missing_mapping_and_timing(annotated_video: VideoFile) -> None:
    ImageClassificationAnnotation.objects.filter(
        annotator="me",
        information_source__name="human_annotation",
        frame__frame_number=0,
    ).update(value=False)
    Frame.objects.filter(video=annotated_video, frame_number=2).update(timestamp=None)
    with patch(
        "endoreg_db.services.annotations.comparisons._mapping", return_value=None
    ):
        result = video_comparisons(
            annotated_video,
            principal="me",
            reports=PatientExaminationReport.objects.none(),
        )
    assert result.conflicts == 1
    assert result.annotations[0].missing == 1
    assert result.annotations[0].overlap is None
    assert result.unavailable == ["missing_mapping"]


@pytest.mark.django_db
def test_human_confirmed_model_annotations_remain_attributed(
    annotated_video: VideoFile,
) -> None:
    model = AiModel.objects.create(name="comparison-model")
    labelset = LabelSet.objects.create(name="comparison-labelset", version=1)
    metadata = ModelMeta.objects.create(
        name="comparison-meta", version="1", model=model, labelset=labelset
    )
    ImageClassificationAnnotation.objects.filter(annotator="peer").update(
        model_meta=metadata
    )
    with patch(
        "endoreg_db.services.annotations.comparisons._mapping", return_value=mapping()
    ):
        result = video_comparisons(
            annotated_video,
            principal="me",
            reports=PatientExaminationReport.objects.none(),
        )
    assert [row.practitioner for row in result.annotations] == ["peer"]
    assert result.annotations[0].agreement == 50


@pytest.mark.django_db
def test_existing_segment_validation_assignments_produce_named_time_overlap(
    annotated_video: VideoFile,
) -> None:
    ImageClassificationAnnotation.objects.all().delete()
    for author, end in (("me", 2), ("peer", 1)):
        segment = LabelVideoSegment.objects.create(
            video_file=annotated_video,
            label=Label.objects.get(name="polyp"),
            source=InformationSource.objects.get(name="manual_annotation"),
            start_frame_number=0,
            end_frame_number=end,
        )
        ensure_segment_annotations(segment_ids=[int(segment.pk)], annotator=author)
    with patch(
        "endoreg_db.services.annotations.comparisons._mapping", return_value=mapping()
    ):
        result = video_comparisons(
            annotated_video,
            principal="me",
            reports=PatientExaminationReport.objects.none(),
        )
    comparison = result.annotations[0]
    assert comparison.practitioner == "peer"
    assert comparison.overlap is not None and isclose(comparison.overlap, 25)
    assert comparison.agreement == 100
    assert comparison.shared == 1
    assert result.findings[0].matching == ["colon_polyp"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "condition, reason",
    [
        ("unattributed", "report_missing_attribution_or_snapshot"),
        ("snapshot_missing", "report_missing_attribution_or_snapshot"),
        ("different_version", "report_terminology_mismatch"),
        ("own_report", None),
        ("inactive", None),
    ],
)
def test_ineligible_reports_are_not_compared(
    annotated_video: VideoFile, condition: str, reason: str | None
) -> None:
    author = User.objects.create_user(
        username="me" if condition == "own_report" else "peer"
    )
    PatientExaminationReport.objects.create(
        patient_examination=annotated_video.examination,
        template_name="eligibility",
        status="final",
        finalized_by=None if condition == "unattributed" else author,
        is_active=condition != "inactive",
        dtypes_record=None
        if condition == "snapshot_missing"
        else {
            "patient": "1",
            "examination": "colonoscopy",
            "knowledge_base_module": "coloreg",
            "knowledge_base_version": "2.0"
            if condition == "different_version"
            else "1.0",
        },
    )
    with patch(
        "endoreg_db.services.annotations.comparisons._mapping", return_value=mapping()
    ):
        result = video_comparisons(
            annotated_video,
            principal="me",
            reports=PatientExaminationReport.objects.all(),
        )
    assert not any(row.kind == "report" for row in result.findings)
    assert result.unavailable == ([reason] if reason else [])


@pytest.mark.django_db
def test_unmapped_own_labels_do_not_become_zero_agreement(
    annotated_video: VideoFile,
) -> None:
    empty = mapping().model_copy(update={"mappings": []})
    with patch(
        "endoreg_db.services.annotations.comparisons._mapping", return_value=empty
    ):
        result = video_comparisons(
            annotated_video,
            principal="me",
            reports=PatientExaminationReport.objects.all(),
        )
    assert result.unmapped == ["polyp"]
    assert result.findings == []
    assert result.unavailable == ["no_mapped_positive_findings"]


@pytest.mark.django_db
def test_endpoint_requires_authentication_and_rejects_principal_override() -> None:
    factory = APIRequestFactory()
    view = AnnotationComparisonsView.as_view()
    assert view(factory.get("/")).status_code in (401, 403)
    user = User.objects.create_user(username="me")
    for query in ("?annotator=peer", "?page=0", "?page=no"):
        request = factory.get("/" + query)
        force_authenticate(request, user=user)
        assert view(request).status_code == 400


@pytest.mark.django_db
def test_endpoint_scopes_videos_and_reports(annotated_video: VideoFile) -> None:
    user = User.objects.create_user(username="me")
    request = APIRequestFactory().get("/")
    force_authenticate(request, user=user)
    with (
        patch(
            "endoreg_db.views.access_control._allowed_center_ids",
            return_value=frozenset(),
        ),
        patch("endoreg_db.views.access_control.hub_mode_enabled", return_value=False),
    ):
        response = AnnotationComparisonsView.as_view()(request)
    assert response.status_code == 200
    assert response.data["count"] == 0
    assert response.data["total"] == 0
    with (
        patch(
            "endoreg_db.views.access_control._allowed_center_ids",
            return_value=frozenset({annotated_video.center_id}),
        ),
        patch(
            "endoreg_db.services.annotations.comparisons._mapping",
            return_value=mapping(),
        ),
    ):
        response = AnnotationComparisonsView.as_view()(request)
    assert response.status_code == 200
    assert response.data["practitioner"] == "me"
    assert response.data["count"] == 1
    assert response.data["total"] == 2
    assert response.data["results"][0]["annotations"][0]["practitioner"] == "peer"


@pytest.mark.django_db
def test_prediction_ranges_are_deduplicated_read_only_and_timestamp_weighted(
    annotated_video: VideoFile,
) -> None:
    for _ in range(2):
        LabelVideoSegment.objects.create(
            video_file=annotated_video,
            label=Label.objects.get(name="polyp"),
            source=InformationSource.objects.get(name="prediction"),
            start_frame_number=0,
            end_frame_number=1,
        )
    before = ImageClassificationAnnotation.objects.count()
    with patch(
        "endoreg_db.services.annotations.comparisons._mapping", return_value=mapping()
    ):
        result = video_comparisons(
            annotated_video,
            principal="me",
            reports=PatientExaminationReport.objects.none(),
        )
    assert len(result.predictions) == 1
    prediction = result.predictions[0]
    assert prediction.practitioner == "KI ohne Laufzuordnung"
    assert prediction.overlap is not None and isclose(prediction.overlap, 25)
    assert prediction.union is not None and isclose(prediction.union, 0.4)
    assert ImageClassificationAnnotation.objects.count() == before
    Frame.objects.filter(video=annotated_video, frame_number=2).update(timestamp=None)
    with patch(
        "endoreg_db.services.annotations.comparisons._mapping", return_value=mapping()
    ):
        missing = video_comparisons(
            annotated_video,
            principal="me",
            reports=PatientExaminationReport.objects.none(),
        )
    assert missing.predictions[0].overlap is None
    assert missing.predictions[0].missing == 1


@pytest.mark.django_db
def test_manual_segments_are_not_ai_and_no_predictions_is_unavailable(
    annotated_video: VideoFile,
) -> None:
    LabelVideoSegment.objects.create(
        video_file=annotated_video,
        label=Label.objects.get(name="polyp"),
        source=InformationSource.objects.get(name="manual_annotation"),
        start_frame_number=0,
        end_frame_number=1,
    )
    with patch(
        "endoreg_db.services.annotations.comparisons._mapping", return_value=mapping()
    ):
        result = video_comparisons(
            annotated_video,
            principal="me",
            reports=PatientExaminationReport.objects.none(),
        )
    assert result.predictions == []


@pytest.mark.django_db
def test_prediction_runs_stay_separate_and_conflicts_are_excluded(
    annotated_video: VideoFile,
) -> None:
    model = AiModel.objects.create(name="comparison-ai")
    labels = LabelSet.objects.create(name="comparison-ai-labels", version=1)
    for version, start, end in (("1", 0, 1), ("2", 1, 2)):
        metadata = ModelMeta.objects.create(
            name="comparison-ai-meta", version=version, model=model, labelset=labels
        )
        run = VideoPredictionMeta.objects.create(
            model_meta=metadata, video_file=annotated_video
        )
        LabelVideoSegment.objects.create(
            video_file=annotated_video,
            label=Label.objects.get(name="polyp"),
            prediction_meta=run,
            start_frame_number=start,
            end_frame_number=end,
        )
    with patch(
        "endoreg_db.services.annotations.comparisons._mapping", return_value=mapping()
    ):
        result = video_comparisons(
            annotated_video,
            principal="me",
            reports=PatientExaminationReport.objects.none(),
        )
    assert len(result.predictions) == 2
    assert result.predictions[0].overlap is not None and isclose(
        result.predictions[0].overlap, 25
    )
    assert result.predictions[1].overlap is not None and isclose(
        result.predictions[1].overlap, 75
    )
    ImageClassificationAnnotation.objects.filter(
        annotator="me",
        information_source__name="human_annotation",
        frame__frame_number=0,
    ).update(value=False)
    with patch(
        "endoreg_db.services.annotations.comparisons._mapping", return_value=mapping()
    ):
        result = video_comparisons(
            annotated_video,
            principal="me",
            reports=PatientExaminationReport.objects.none(),
        )
    assert result.predictions[0].overlap == 0
    assert result.predictions[1].overlap == 100

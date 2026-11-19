from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate

from endoreg_db.models import (
    Center,
    Frame,
    FrameBoxAnnotation,
    InformationSource,
    ImageClassificationAnnotation,
    Label,
    LabelVideoSegment,
    VideoFile,
)
from endoreg_db.models.other.study_cohort import StudyCohort
from endoreg_db.services.annotations.label_presentation import require_study_labels
from endoreg_db.views.video.ai.frame_annotations import FrameAnnotationBulkUpsertView
from endoreg_db.views.video.ai.frame_box_annotations import FrameBoxAnnotationView
from endoreg_db.views.video.ai.label import label_catalog
from endoreg_db.views.video.segments_crud import (
    import_prediction_segments_to_manual,
    video_segment_detail,
    video_segment_validate,
    video_segments_bulk_mutation,
    video_segments_collection,
    video_segments_validate_bulk,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner() -> User:
    return User.objects.create_superuser(username="label-owner", password="test-only")


@pytest.fixture
def cohort(owner: User) -> StudyCohort:
    Label.objects.create(name="study_allowed")
    Label.objects.create(name="study_forbidden")
    return StudyCohort.objects.create(
        created_by=owner,
        name="Labels",
        hypothesis="Label presentation",
        study_metadata={
            "label_presentation": {
                "locked": True,
                "labels": [
                    {
                        "name": "study_allowed",
                        "display_name": "Preferred label",
                        "color": "#112233",
                    }
                ],
                "scopes": [
                    {
                        "name": "expert",
                        "display_name": "Expert",
                        "labels": ["study_allowed"],
                    }
                ],
            }
        },
    )


def test_catalog_configured_display_and_reload(
    owner: User, cohort: StudyCohort
) -> None:
    factory = APIRequestFactory()
    request = factory.get("/labels/catalog/", {"study_cohort_id": cohort.pk})
    force_authenticate(request, owner)
    response = label_catalog(request)
    assert response.status_code == 200
    assert response.data["labels"][0]["name"] == "study_allowed"
    assert response.data["labels"][0]["display_name"] == "Preferred label"
    assert len(response.data["labels"]) == 1
    assert response["Cache-Control"] == "private, no-store"
    cohort.study_metadata = {
        "label_presentation": {
            "locked": False,
            "labels": [{"name": "study_forbidden", "display_name": "Reloaded"}],
        }
    }
    cohort.save()
    response = label_catalog(request)
    assert response.data["labels"][0]["display_name"] == "Reloaded"
    assert any(item["name"] == "study_allowed" for item in response.data["labels"])


def test_catalog_rejects_foreign_cohort_and_invalid_id(cohort: StudyCohort) -> None:
    other = User.objects.create_superuser(username="label-other", password="test-only")
    factory = APIRequestFactory()
    request = factory.get("/labels/catalog/", {"study_cohort_id": cohort.pk})
    force_authenticate(request, other)
    assert label_catalog(request).status_code == 404
    request = factory.get("/labels/catalog/", {"study_cohort_id": "-1"})
    assert label_catalog(request).status_code == 400


def test_unscoped_guard_never_queries() -> None:
    request = APIRequestFactory().get("/labels/catalog/")
    with CaptureQueriesContext(connection) as captured:
        require_study_labels(request, None, label_ids=[123])
    assert len(captured) == 0


def test_unscoped_catalog_and_invalid_saved_reference(
    owner: User, cohort: StudyCohort
) -> None:
    request = APIRequestFactory().get("/labels/catalog/")
    response = label_catalog(request)
    assert response.status_code == 200
    assert response.data["locked"] is False
    assert len(response.data["labels"]) >= 2
    Label.objects.filter(name="study_allowed").delete()
    request = APIRequestFactory().get(
        "/labels/catalog/", {"study_cohort_id": cohort.pk}
    )
    force_authenticate(request, owner)
    assert label_catalog(request).status_code == 400


def test_forbidden_frame_and_segment_creation_reject_before_write(
    owner: User, cohort: StudyCohort
) -> None:
    forbidden = Label.objects.get(name="study_forbidden")
    request = APIRequestFactory().post(
        "/frames/bulk/",
        {
            "study_cohort_id": cohort.pk,
            "annotations": [
                {
                    "frame_id": 999,
                    "label_id": forbidden.pk,
                    "value": True,
                    "information_source_name": "manual_annotation",
                }
            ],
        },
        format="json",
    )
    force_authenticate(request, owner)
    with patch(
        "endoreg_db.views.video.ai.frame_annotations._persist_bulk_annotations"
    ) as persist:
        assert FrameAnnotationBulkUpsertView.as_view()(request).status_code == 400
        persist.assert_not_called()
    request = APIRequestFactory().post(
        "/segments/",
        {
            "study_cohort_id": cohort.pk,
            "video_id": 999,
            "label_id": forbidden.pk,
            "start_frame_number": 0,
            "end_frame_number": 2,
        },
        format="json",
    )
    force_authenticate(request, owner)
    assert video_segments_collection(request).status_code == 400


def test_unscoped_frame_submission_keeps_existing_path() -> None:
    request = APIRequestFactory().post(
        "/frames/bulk/",
        {
            "annotations": [
                {
                    "frame_id": 999,
                    "label_id": 999,
                    "value": True,
                    "information_source_name": "manual_annotation",
                }
            ],
        },
        format="json",
    )
    with patch(
        "endoreg_db.views.video.ai.frame_annotations._build_bulk_upsert_response",
        return_value=Response({"ok": True}),
    ) as persist:
        response = FrameAnnotationBulkUpsertView.as_view()(request)
    assert response.status_code == 200
    persist.assert_called_once()


@pytest.mark.parametrize("by_choice", [False, True])
def test_locked_study_allowed_frame_write_keeps_label_identity(
    owner: User, cohort: StudyCohort, by_choice: bool
) -> None:
    center = Center.objects.create(name="study-write-center")
    video = VideoFile.objects.create(
        center=center, raw_video_hash="study-write-video", fps=25, frame_count=2
    )
    frame = Frame.objects.create(video=video, frame_number=0, relative_path="frame.jpg")
    source = InformationSource.objects.create(name="manual_annotation")
    allowed = Label.objects.get(name="study_allowed")
    item: dict[str, object] = {
        "frame_id": frame.pk,
        "value": True,
        "information_source_name": source.name,
    }
    item["choice_name" if by_choice else "label_id"] = (
        allowed.name if by_choice else allowed.pk
    )
    request = APIRequestFactory().post(
        "/frames/bulk/",
        {"study_cohort_id": cohort.pk, "annotations": [item]},
        format="json",
    )
    force_authenticate(request, owner)
    response = FrameAnnotationBulkUpsertView.as_view()(request)
    assert response.status_code == 200, response.content
    assert (
        ImageClassificationAnnotation.objects.filter(
            frame=frame, label=allowed, annotator="label-owner", value=True
        ).count()
        == 1
    )


@pytest.mark.parametrize(
    "operation", ["edit", "single_validate", "bulk_validate", "bulk_edit", "import"]
)
def test_forbidden_segment_mutations_preserve_existing_annotation(
    owner: User, cohort: StudyCohort, operation: str
) -> None:
    center = Center.objects.create(name="study-segment-center")
    video = VideoFile.objects.create(
        center=center, raw_video_hash="study-segment-video", fps=25, frame_count=10
    )
    forbidden = Label.objects.get(name="study_forbidden")
    segment = LabelVideoSegment.objects.create(
        video_file=video, label=forbidden, start_frame_number=0, end_frame_number=2
    )
    factory = APIRequestFactory()
    body: dict[str, object] = {"study_cohort_id": cohort.pk}
    if operation == "edit":
        body["label_id"] = forbidden.pk
        request = factory.patch("/segments/", body, format="json")
        force_authenticate(request, owner)
        response = video_segment_detail(
            request, pk=int(video.pk), segment_id=int(segment.pk)
        )
    else:
        if operation == "bulk_validate":
            body["segment_ids"] = [segment.pk]
        elif operation == "bulk_edit":
            body["updates"] = [{"id": segment.pk, "start_time": 0, "end_time": 0.08}]
        elif operation == "import":
            body["segments"] = [
                {"label_name": forbidden.name, "start_time": 0, "end_time": 0.08}
            ]
        request = factory.post("/segments/", body, format="json")
        force_authenticate(request, owner)
        if operation == "single_validate":
            response = video_segment_validate(
                request, pk=int(video.pk), segment_id=int(segment.pk)
            )
        elif operation == "bulk_validate":
            response = video_segments_validate_bulk(request, pk=int(video.pk))
        elif operation == "bulk_edit":
            response = video_segments_bulk_mutation(request, pk=int(video.pk))
        else:
            response = import_prediction_segments_to_manual(request, pk=int(video.pk))
    assert response.status_code == 400
    segment.refresh_from_db()
    assert LabelVideoSegment.objects.filter(pk=segment.pk, label=forbidden).exists()
    assert segment.end_frame_number == 2


@pytest.mark.parametrize("empty", [True, False])
def test_box_replace_preserves_historical_forbidden_labels(
    owner: User, cohort: StudyCohort, empty: bool
) -> None:
    center = Center.objects.create(name="study-label-center")
    video = VideoFile.objects.create(
        center=center, raw_video_hash="study-label-video", fps=25, frame_count=2
    )
    frame = Frame.objects.create(video=video, frame_number=0, relative_path="frame.jpg")
    source = InformationSource.objects.create(name="manual_annotation")
    old = FrameBoxAnnotation.objects.create(
        frame=frame,
        label=Label.objects.get(name="study_forbidden"),
        information_source=source,
        annotator="label-owner",
        x=1,
        y=1,
        width=2,
        height=2,
        image_width=10,
        image_height=10,
    )
    annotations = (
        []
        if empty
        else [
            {
                "label_id": Label.objects.get(name="study_allowed").pk,
                "x": 2,
                "y": 2,
                "width": 2,
                "height": 2,
                "image_width": 10,
                "image_height": 10,
            }
        ]
    )
    request = APIRequestFactory().post(
        "/frames/boxes/",
        {
            "study_cohort_id": cohort.pk,
            "frame_id": frame.pk,
            "video_id": video.pk,
            "replace": True,
            "information_source_name": source.name,
            "annotator": "label-owner",
            "annotations": annotations,
        },
        format="json",
    )
    force_authenticate(request, owner)
    assert FrameBoxAnnotationView.as_view()(request).status_code == 400
    assert FrameBoxAnnotation.objects.filter(pk=old.pk).exists()
    assert FrameBoxAnnotation.objects.filter(frame=frame).count() == 1

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase
from pydantic import BaseModel, ConfigDict
from rest_framework.test import APIRequestFactory

from endoreg_db.models import (
    Center,
    InformationSource,
    Label,
    LabelVideoSegment,
    VideoFile,
)
from endoreg_db.services.jobs.video_fps_normalization_jobs import (
    FpsNormalizationDispatchResult,
)
from endoreg_db.views.video.segments_crud import (
    video_segments_normalize_fps,
    video_segments_validation_status,
)


class _ValidationCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    validated: int


class _ValidationStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: int
    total_segments: int
    validated_count: int
    unvalidated_count: int
    validation_complete: bool
    by_label: dict[str, _ValidationCounts]
    label_filter: str | None


class _ValidationUpdateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    video_id: int
    total_segments: int
    updated_count: int
    failed_count: int
    label_filter: str | None
    validation_status: str
    annotation_expansion_task_id: str


class _EmptyValidationUpdateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    video_id: int
    updated_count: int


class _FpsNormalizationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: int
    status: str
    fps: float | None
    max_fps: float
    task_id: str
    history_id: int | None
    detail: str


class _FpsNormalizationErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: str
    detail: str
    status: str
    video_id: int


class VideoSegmentValidationStatusViewTest(TestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        center = Center.objects.create(name="Validation status center")
        self.video = VideoFile.objects.create(
            center=center,
            video_hash="validation-status-video",
            original_file_name="validation_status.mp4",
            fps=25.0,
            frame_count=500,
        )
        self.source = InformationSource.objects.create(name="manual_annotation")
        self.outside = Label.objects.create(name="outside")
        self.polyp = Label.objects.create(name="polyp")

    def _create_segment(
        self,
        *,
        label: Label,
        start_frame: int,
        validated: bool,
    ) -> LabelVideoSegment:
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=label,
            source=self.source,
            start_frame_number=start_frame,
            end_frame_number=start_frame + 10,
        )
        if validated:
            segment.mark_validated(
                is_validated=True,
                information_source_name=self.source.name,
            )
        return segment

    def test_get_aggregates_validation_counts_by_label(self) -> None:
        self._create_segment(label=self.outside, start_frame=0, validated=True)
        self._create_segment(label=self.outside, start_frame=20, validated=False)
        self._create_segment(label=self.polyp, start_frame=40, validated=True)
        request = self.factory.get(
            f"/api/media/videos/{self.video.pk}/segments/validation-status/"
        )

        response = video_segments_validation_status(request, pk=int(self.video.pk))

        assert response.status_code == 200
        payload = _ValidationStatusResponse.model_validate_json(response.content)
        assert payload.video_id == self.video.pk
        assert payload.total_segments == 3
        assert payload.validated_count == 2
        assert payload.unvalidated_count == 1
        assert payload.validation_complete is False
        assert payload.by_label == {
            "outside": _ValidationCounts(total=2, validated=1),
            "polyp": _ValidationCounts(total=1, validated=1),
        }
        assert payload.label_filter is None

    def test_get_applies_label_filter_before_aggregation(self) -> None:
        self._create_segment(label=self.outside, start_frame=0, validated=False)
        self._create_segment(label=self.polyp, start_frame=20, validated=True)
        request = self.factory.get(
            f"/api/media/videos/{self.video.pk}/segments/validation-status/",
            {"label_name": "polyp"},
        )

        response = video_segments_validation_status(request, pk=int(self.video.pk))

        assert response.status_code == 200
        payload = _ValidationStatusResponse.model_validate_json(response.content)
        assert payload.total_segments == 1
        assert payload.validated_count == 1
        assert payload.validation_complete is True
        assert payload.by_label == {"polyp": _ValidationCounts(total=1, validated=1)}
        assert payload.label_filter == "polyp"

    def test_get_empty_video_is_not_validation_complete(self) -> None:
        request = self.factory.get(
            f"/api/media/videos/{self.video.pk}/segments/validation-status/"
        )

        response = video_segments_validation_status(request, pk=int(self.video.pk))

        assert response.status_code == 200
        payload = _ValidationStatusResponse.model_validate_json(response.content)
        assert payload.total_segments == 0
        assert payload.validated_count == 0
        assert payload.unvalidated_count == 0
        assert payload.validation_complete is False
        assert payload.by_label == {}

    def test_post_empty_video_returns_noop_without_dispatch(self) -> None:
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/validation-status/",
            {},
            format="json",
        )

        with patch(
            "endoreg_db.views.video.segments_crud._dispatch_segment_annotation_expansion"
        ) as dispatch:
            response = video_segments_validation_status(request, pk=int(self.video.pk))

        assert response.status_code == 200
        payload = _EmptyValidationUpdateResponse.model_validate_json(response.content)
        assert payload.video_id == self.video.pk
        assert payload.updated_count == 0
        dispatch.assert_not_called()

    def test_post_validates_filtered_segments_and_returns_typed_task_id(self) -> None:
        outside_segment = self._create_segment(
            label=self.outside,
            start_frame=0,
            validated=False,
        )
        polyp_segment = self._create_segment(
            label=self.polyp,
            start_frame=20,
            validated=False,
        )
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/validation-status/",
            {"label_name": "outside"},
            format="json",
        )

        with (
            patch(
                "endoreg_db.views.video.segments_crud._dispatch_segment_annotation_expansion",
                return_value=("annotation-task", False),
            ) as dispatch,
            patch(
                "endoreg_db.views.video.segments_crud.mark_segment_annotations_pending_cleanup"
            ),
        ):
            response = video_segments_validation_status(request, pk=int(self.video.pk))

        assert response.status_code == 202
        payload = _ValidationUpdateResponse.model_validate_json(response.content)
        assert payload.total_segments == 1
        assert payload.updated_count == 1
        assert payload.failed_count == 0
        assert payload.label_filter == "outside"
        assert payload.validation_status == "annotation_expansion_queued"
        assert payload.annotation_expansion_task_id == "annotation-task"
        dispatch.assert_called_once_with(
            video_id=int(self.video.pk),
            segment_ids=[int(outside_segment.pk)],
            information_source_name="manual_annotation",
            annotator=None,
            dispatch_post_validation_rebuild=True,
        )
        outside_segment.state.refresh_from_db()
        polyp_segment.state.refresh_from_db()
        assert outside_segment.state.is_validated is True
        assert polyp_segment.state.is_validated is False


class VideoSegmentFpsNormalizationStatusViewTest(TestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        center = Center.objects.create(name="Normalization status center")
        self.video = VideoFile.objects.create(
            center=center,
            video_hash="normalization-status-video",
            original_file_name="normalization_status.mp4",
            fps=60.0,
            frame_count=600,
        )

    @patch("endoreg_db.views.video.segments_crud.normalization_status")
    def test_get_returns_current_normalization_status(
        self,
        normalization_status: MagicMock,
    ) -> None:
        normalization_status.return_value = FpsNormalizationDispatchResult(
            video_id=int(self.video.pk),
            status="running",
            fps=60.0,
            max_fps=50.0,
            task_id="normalization-task",
            history_id=17,
        )
        request = self.factory.get(
            f"/api/media/videos/{self.video.pk}/segments/normalize-fps/"
        )

        response = video_segments_normalize_fps(request, pk=int(self.video.pk))

        assert response.status_code == 202
        payload = _FpsNormalizationResponse.model_validate_json(response.content)
        assert payload.status == "running"
        assert payload.task_id == "normalization-task"
        assert payload.history_id == 17
        normalization_status.assert_called_once_with(self.video)

    @patch("endoreg_db.views.video.segments_crud.normalization_status")
    def test_get_returns_typed_error_for_invalid_source_fps(
        self,
        normalization_status: MagicMock,
    ) -> None:
        normalization_status.side_effect = ValueError("source fps is missing")
        request = self.factory.get(
            f"/api/media/videos/{self.video.pk}/segments/normalize-fps/"
        )

        response = video_segments_normalize_fps(request, pk=int(self.video.pk))

        assert response.status_code == 422
        payload = _FpsNormalizationErrorResponse.model_validate_json(response.content)
        assert payload.error == "Could not determine a valid source FPS."
        assert payload.detail == "source fps is missing"
        assert payload.status == "failed"
        assert payload.video_id == self.video.pk

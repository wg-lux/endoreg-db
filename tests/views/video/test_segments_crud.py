import os
import types
from collections.abc import Callable, Mapping
from typing import Any, cast
from typing import Protocol
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import resolve
from rest_framework.test import APIRequestFactory
import json

# Adjust imports based on your actual project structure
from endoreg_db.models import (
    AIDataSet,
    Center,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    LabelVideoSegment,
    VideoFile,
    VideoProcessingHistory,
)
from endoreg_db.models.state import video_segment_validation as segment_state
from endoreg_db.serializers import LabelVideoSegmentSerializer
from endoreg_db.serializers.video.video_file_list import VideoFileListSerializer
from endoreg_db.services.jobs import video_post_validation_jobs as post_validation_jobs
from endoreg_db.services.jobs.video_post_validation_jobs import JobDispatchResult
from endoreg_db.services.jobs.video_fps_normalization_jobs import (
    FpsNormalizationDispatchResult,
)
from endoreg_db.views.video.segments_crud import (
    ensure_prediction_segment_annotations_for_video,
    import_prediction_segments_to_manual,
    video_segment_validate,
    video_segments_blacken_outside,
    video_segments_normalize_fps,
    video_segments_bulk_mutation,
    video_segments_by_video,
    video_segments_validate_bulk,
)


class VideoSegmentFpsNormalizationViewTest(TestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        center = Center.objects.create(name="FPS normalization center")
        self.video = VideoFile.objects.create(
            center=center,
            video_hash="fps-normalization-video",
            original_file_name="fps-normalization.mp4",
            fps=60.0,
            frame_count=600,
        )

    @patch("endoreg_db.views.video.segments_crud.dispatch_video_fps_normalization")
    def test_post_dispatches_non_blocking_normalization(
        self, dispatch: MagicMock
    ) -> None:
        dispatch.return_value = FpsNormalizationDispatchResult(
            video_id=int(self.video.pk),
            status="queued",
            fps=60.0,
            max_fps=50.0,
            task_id="fps-task",
            history_id=7,
        )
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/normalize-fps/",
            {},
            format="json",
        )

        response = video_segments_normalize_fps(request, pk=int(self.video.pk))

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], "queued")
        self.assertEqual(response.data["max_fps"], 50.0)
        dispatch.assert_called_once_with(self.video)


class _SerializerErrorsCarrier(Protocol):
    errors: Mapping[str, object]


class _SerializerDataCarrier(Protocol):
    data: Mapping[str, object]


def _serializer_errors(serializer: object) -> Mapping[str, object]:
    return cast(_SerializerErrorsCarrier, serializer).errors


def _serializer_data(serializer: object) -> Mapping[str, object]:
    return cast(_SerializerDataCarrier, serializer).data


class LabelVideoSegmentSerializerTest(TestCase):
    def setUp(self):
        # 1. Setup minimal dependencies
        self.center = Center.objects.create(name="Test Center")
        self.source = InformationSource.objects.create(name="Manual Annotation")

        # 2. Create a Video with known FPS (30.0 makes math easy)
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="test_hash_123",
            original_file_name="test_video.mp4",
            fps=30.0,
            frame_count=1000,
            duration=33.33,
        )

        # 3. Create a Label
        self.label_polyp = Label.objects.create(name="polyp")

        # 4. Create a dummy segment
        self.segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label_polyp,
            start_frame_number=0,
            end_frame_number=30,  # 1 second
            source=self.source,
        )

    def test_update_time_precision_from_frontend_drag(self):
        """
        Scenario: User drags a handle in Timeline.vue.
        videoStore.ts -> updateSegmentAPI sends 'start_time' as a float.

        Mathematical Edge Case:
        Frame 7 at 30 FPS is exactly 0.2333333333 seconds.
        0.2333333333 * 30.0 = 6.999999999.

        If backend uses int(), it becomes Frame 6 (WRONG).
        If backend uses round(), it becomes Frame 7 (CORRECT).
        """
        target_frame = 7
        fps = 30.0

        # Simulate time sent from frontend (float precision)
        calculated_time = target_frame / fps

        payload = {
            "start_time": calculated_time,
            # Frontend might send end_time same as old one, but required by logic validation
            "end_time": self.segment.end_time,
        }
        serializer = LabelVideoSegmentSerializer(
            instance=self.segment, data=payload, partial=True
        )
        self.assertTrue(serializer.is_valid(), str(_serializer_errors(serializer)))
        updated_segment = serializer.save()

        self.assertEqual(updated_segment.start_frame_number, 7)

    def test_partial_time_update_reuses_existing_boundary(self):
        payload = {
            "end_time": self.segment.start_time,
        }
        serializer = LabelVideoSegmentSerializer(
            instance=self.segment, data=payload, partial=True
        )
        self.assertFalse(serializer.is_valid())
        # Fix: Cast errors to a dict to allow safe membership check
        self.assertIn("end_time", _serializer_errors(serializer))

    def test_video_store_creation_payload(self):
        """
        Scenario: videoStore.ts -> createSegment
        The store calculates frames on the client side using Math.floor and sends frame numbers directly.
        """
        # Guard against order-dependent side effects from other tests mutating/deleting
        # global fixtures/models while keeping this test intention intact.
        self.video.refresh_from_db()
        self.assertTrue(
            VideoFile.objects.filter(pk=self.video.pk).exists(),
            "Test setup video must exist before serializer.create()",
        )
        payload = {
            "video_id": self.video.pk,
            "label_id": self.label_polyp.pk,
            # Frontend sends calculated frames
            "start_frame_number": 90,
            "end_frame_number": 120,
        }

        serializer = LabelVideoSegmentSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), str(_serializer_errors(serializer)))
        new_segment = serializer.save()

        self.assertEqual(new_segment.start_frame_number, 90)
        self.assertEqual(new_segment.end_frame_number, 120)
        self.assertEqual(new_segment.video_file, self.video)

    def test_performance_get_time_segments_n_plus_one(self):
        """
        Scenario: Loading the Timeline.
        The serializer method `get_time_segments` iterates over frames.
        We must ensure it doesn't fire a database query for every single frame
        to get annotations. Two fixed queries additionally resolve the
        authoritative start and end presentation timestamps.
        """
        frames: list[Frame] = []
        for i in range(50):
            frames.append(Frame(video=self.video, frame_number=i))
        Frame.objects.bulk_create(frames)

        self.segment.end_frame_number = 50
        self.segment.save()

        saved_frames = Frame.objects.filter(video=self.video)
        # Fix: Explicitly type the annotations list
        annotations: list[ImageClassificationAnnotation] = []
        for frame in saved_frames:
            annotations.append(
                ImageClassificationAnnotation(
                    frame=frame,
                    label=self.label_polyp,
                    information_source=self.source,
                    value=True,
                )
            )
        ImageClassificationAnnotation.objects.bulk_create(annotations)
        if frames:
            # Fix: Avoid type checking unknown attributes on Frame
            _ = cast(Any, frames[0]).file_path

        with self.assertNumQueries(4):
            serializer = LabelVideoSegmentSerializer(self.segment)
            # Fix: Cast self.segment to Any to satisfy LabelVideoSegmentLike Protocol
            # and explicitly cast the return dictionary structure
            data = cast(
                dict[str, Any], serializer.get_time_segments(cast(Any, self.segment))
            )

            self.assertEqual(len(data["frames"]), 50)
            self.assertTrue(len(data["frames"][0]["all_classifications"]) > 0)

    def test_video_list_serializer_exposes_validated_annotators_only_after_validation(
        self,
    ):
        frames = [
            Frame(video=self.video, frame_number=frame_number)
            for frame_number in range(3)
        ]
        Frame.objects.bulk_create(frames)
        saved_frames = list(Frame.objects.filter(video=self.video).order_by("id"))
        ImageClassificationAnnotation.objects.bulk_create(
            [
                ImageClassificationAnnotation(
                    frame=saved_frames[0],
                    label=self.label_polyp,
                    information_source=self.source,
                    value=True,
                    annotator="reviewer-two",
                ),
                ImageClassificationAnnotation(
                    frame=saved_frames[1],
                    label=self.label_polyp,
                    information_source=self.source,
                    value=True,
                    annotator="reviewer-one",
                ),
                ImageClassificationAnnotation(
                    frame=saved_frames[2],
                    label=self.label_polyp,
                    information_source=self.source,
                    value=True,
                    annotator="",
                ),
            ]
        )

        serializer = VideoFileListSerializer(self.video)
        data = _serializer_data(serializer)
        self.assertEqual(data["validated_annotators"], [])

        state = self.video.get_or_create_state()
        state.segment_annotations_validated = True
        state.outside_segments_removed = True
        state.save(
            update_fields=[
                "segment_annotations_validated",
                "outside_segments_removed",
                "date_modified",
            ]
        )

        serializer = VideoFileListSerializer(self.video)
        data_after = _serializer_data(serializer)
        self.assertEqual(
            data_after["validated_annotators"], ["reviewer-one", "reviewer-two"]
        )

    def test_video_list_serializer_requires_outside_cleanup_for_final_validation(self):
        state = self.video.get_or_create_state()
        state.segment_annotations_created = True
        state.segment_annotations_validated = True
        state.outside_segments_removed = False
        state.save(
            update_fields=[
                "segment_annotations_created",
                "segment_annotations_validated",
                "outside_segments_removed",
                "date_modified",
            ]
        )

        serializer = VideoFileListSerializer(self.video)

        data = _serializer_data(serializer)

        self.assertFalse(data["segment_annotations_validated"])
        self.assertEqual(data["segment_annotation_status"], "cleanup_required")


class PredictionSegmentAnnotationsRouteTest(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.center = Center.objects.create(name="Prediction Route Center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="pred-anno-route-hash",
            original_file_name="pred_route.mp4",
            fps=25.0,
            frame_count=100,
        )
        self.label = Label.objects.create(name="prediction-route-polyp")
        self.prediction_source = InformationSource.objects.create(name="prediction")
        self.manual_source = InformationSource.objects.create(name="manual_annotation")

        self.segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=5,
            end_frame_number=8,  # frames 5,6,7
            source=self.prediction_source,
            prediction_meta=None,
        )

        self.frames = [
            Frame.objects.create(
                video=self.video,
                frame_number=frame_number,
                relative_path=f"frame_{frame_number}.jpg",
            )
            for frame_number in range(5, 8)
        ]

        # Existing manual annotations must remain untouched (redundant tracks).
        ImageClassificationAnnotation.objects.bulk_create(
            [
                ImageClassificationAnnotation(
                    frame=frame,
                    label=self.label,
                    value=True,
                    information_source=self.manual_source,
                )
                for frame in self.frames
            ]
        )

    def test_prediction_annotation_route_creates_redundant_annotations(self):
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/ensure-prediction-segment-annotations/",
            {},
            format="json",
        )

        response = ensure_prediction_segment_annotations_for_video(
            request, pk=self.video.pk
        )
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 202)
        stats = data["stats"]
        self.assertEqual(stats["eligible_prediction_segments"], 1)
        self.assertEqual(stats["annotations_created"], 3)

        prediction_annotation_source = InformationSource.objects.get(
            name="prediction_annotation"
        )
        self.assertEqual(
            ImageClassificationAnnotation.objects.filter(
                frame__video=self.video,
                label=self.label,
                information_source=self.manual_source,
            ).count(),
            3,
        )
        self.assertEqual(
            ImageClassificationAnnotation.objects.filter(
                frame__video=self.video,
                label=self.label,
                information_source=prediction_annotation_source,
            ).count(),
            3,
        )


class VideoSegmentsByVideoPayloadModeTest(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.center = Center.objects.create(name="Segments Payload Center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="segments-payload-video",
            original_file_name="segments_payload.mp4",
            fps=25.0,
            frame_count=200,
        )
        self.label = Label.objects.create(name="segments-payload-label")
        self.manual_source = InformationSource.objects.create(name="manual_annotation")
        self.segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=10,
            end_frame_number=13,
            source=self.manual_source,
        )
        self.frames = [
            Frame.objects.create(
                video=self.video,
                frame_number=i,
                relative_path=f"frame_{i:07d}.jpg",
            )
            for i in range(10, 13)
        ]
        ImageClassificationAnnotation.objects.bulk_create(
            [
                ImageClassificationAnnotation(
                    frame=frame,
                    label=self.label,
                    value=True,
                    information_source=self.manual_source,
                )
                for frame in self.frames
            ]
        )

    def test_default_list_payload_skips_annotation_expansion(self):
        request = self.factory.get(f"/api/media/videos/{self.video.pk}/segments/")
        response = video_segments_by_video(request, pk=self.video.pk)
        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(data), 1)

        payload = data[0]
        self.assertEqual(payload["manual_frame_annotations"], [])
        self.assertEqual(payload["frame_predictions"], [])
        self.assertEqual(payload["time_segments"]["frames"], [])

    def test_list_payload_can_opt_in_to_annotation_expansion(self):
        request = self.factory.get(
            f"/api/media/videos/{self.video.pk}/segments/?include_annotation_payload=1"
        )
        response = video_segments_by_video(request, pk=self.video.pk)
        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(data), 1)

        payload = data[0]
        self.assertEqual(len(payload["time_segments"]["frames"]), 3)


class VideoSegmentValidateAsyncSafetyTest(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.center = Center.objects.create(name="Validation Async Center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="validate-async-video",
            original_file_name="validate_async.mp4",
            fps=25.0,
            frame_count=100,
        )
        self.label = Label.objects.create(name="validate-async-label")
        self.manual_source = InformationSource.objects.create(name="manual_annotation")
        self.segment_source = InformationSource.objects.create(name="prediction")
        self.segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=10,
            end_frame_number=13,  # frames 10,11,12
            source=self.segment_source,
        )
        self.frames = [
            Frame.objects.create(
                video=self.video,
                frame_number=i,
                relative_path=f"frame_{i:07d}.jpg",
            )
            for i in range(10, 13)
        ]
        ImageClassificationAnnotation.objects.bulk_create(
            [
                ImageClassificationAnnotation(
                    frame=frame,
                    label=self.label,
                    value=True,
                    information_source=self.manual_source,
                )
                for frame in self.frames
            ]
        )

    def test_validation_keeps_manual_frame_annotations_and_queues_job(self):
        before = list(
            ImageClassificationAnnotation.objects.filter(
                frame__video=self.video,
                label=self.label,
                information_source=self.manual_source,
            )
            .order_by("frame__frame_number")
            .values_list("frame__frame_number", flat=True)
        )
        self.assertEqual(before, [10, 11, 12])

        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/{self.segment.pk}/validate/",
            {"is_validated": True, "information_source_name": "manual_annotation"},
            format="json",
        )

        with (
            patch(
                "endoreg_db.views.video.segments_crud.dispatch_video_post_validation_rebuild",
                side_effect=AssertionError(
                    "single-segment validation must not dispatch blackening"
                ),
            ),
            patch(
                "endoreg_db.views.video.segments_crud.record_operation",
                return_value=None,
            ),
        ):
            response = video_segment_validate(
                request, pk=self.video.pk, segment_id=self.segment.pk
            )
        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["validation_status"], "completed")
        self.assertNotIn("post_processing_job", data)

        after = list(
            ImageClassificationAnnotation.objects.filter(
                frame__video=self.video,
                label=self.label,
                information_source=self.manual_source,
            )
            .order_by("frame__frame_number")
            .values_list("frame__frame_number", flat=True)
        )
        self.assertEqual(after, before)

    def test_bulk_validation_passes_explicit_annotator_to_annotation_generation(self):
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/validate-bulk/",
            {
                "segment_ids": [self.segment.pk],
                "segments": [
                    {
                        "id": self.segment.pk,
                        "start_time": self.segment.start_time,
                        "end_time": self.segment.end_time,
                    }
                ],
                "is_validated": True,
                "information_source_name": "manual_annotation",
                "annotator": "reviewer-two",
            },
            format="json",
        )

        with (
            patch(
                "endoreg_db.views.video.segments_crud.record_operation",
                return_value=None,
            ),
        ):
            response = video_segments_validate_bulk(request, pk=self.video.pk)

        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["validation_status"], "completed")
        self.assertEqual(data["post_processing_job"]["status"], "noop")
        self.assertEqual(data["segment_annotation_status"], "validated")
        self.assertTrue(data["segment_annotations_validated"])
        self.assertTrue(data["outside_segments_removed"])
        state = self.video.get_or_create_state()
        state.refresh_from_db()
        self.assertTrue(state.segment_annotations_validated)
        self.assertTrue(state.outside_segments_removed)
        self.assertEqual(
            ImageClassificationAnnotation.objects.filter(
                frame__video=self.video,
                label=self.label,
                information_source=self.manual_source,
                model_meta__isnull=True,
                annotator="reviewer-two",
            ).count(),
            3,
        )

    def test_bulk_validation_queues_cleanup_before_final_validation(self):
        outside_label, _ = Label.objects.get_or_create(name="outside")
        outside_segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=outside_label,
            start_frame_number=10,
            end_frame_number=13,
            source=self.segment_source,
        )
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/validate-bulk/",
            {
                "segment_ids": [outside_segment.pk],
                "segments": [
                    {
                        "id": outside_segment.pk,
                        "start_time": outside_segment.start_time,
                        "end_time": outside_segment.end_time,
                    }
                ],
                "is_validated": True,
                "information_source_name": "manual_annotation",
                "annotator": "reviewer-two",
            },
            format="json",
        )

        submitted: list[Callable[[], None]] = []

        def fake_submit(fn: Callable[[], None]) -> types.SimpleNamespace:
            submitted.append(fn)
            return types.SimpleNamespace()

        with (
            patch.dict(os.environ, {"VIDEO_POST_VALIDATION_JOB_MODE": "thread"}),
            patch.object(
                post_validation_jobs._executor,
                "submit",
                fake_submit,
            ),
            patch(
                "endoreg_db.views.video.segments_crud.record_operation",
                return_value=None,
            ),
        ):
            response = video_segments_validate_bulk(request, pk=self.video.pk)

        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 202)
        self.assertEqual(data["validation_status"], "scheduled")
        self.assertEqual(data["post_processing_job"]["status"], "queued")
        self.assertEqual(data["segment_annotation_status"], "cleanup_queued")
        self.assertFalse(data["segment_annotations_validated"])
        self.assertFalse(data["outside_segments_removed"])
        self.assertEqual(len(submitted), 1)
        state = self.video.get_or_create_state()
        state.refresh_from_db()
        self.assertTrue(state.segment_annotations_created)
        self.assertFalse(state.segment_annotations_validated)
        self.assertFalse(state.outside_segments_removed)
        history = VideoProcessingHistory.objects.get(
            pk=data["post_processing_job"]["history_id"]
        )
        self.assertEqual(history.status, VideoProcessingHistory.STATUS_PENDING)

    def test_bulk_validation_dispatch_failure_stays_non_final(self):
        outside_label, _ = Label.objects.get_or_create(name="outside")
        outside_segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=outside_label,
            start_frame_number=10,
            end_frame_number=13,
            source=self.segment_source,
        )
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/validate-bulk/",
            {
                "segment_ids": [outside_segment.pk],
                "segments": [
                    {
                        "id": outside_segment.pk,
                        "start_time": outside_segment.start_time,
                        "end_time": outside_segment.end_time,
                    }
                ],
                "is_validated": True,
                "information_source_name": "manual_annotation",
            },
            format="json",
        )

        def failed_dispatch(*, video_id: int, only_validated: bool = False):
            history = VideoProcessingHistory.objects.create(
                video_id=video_id,
                operation=VideoProcessingHistory.OPERATION_REPROCESSING,
                status=VideoProcessingHistory.STATUS_FAILURE,
                task_id="failed-cleanup-task",
                details="broker unavailable",
                config=segment_state.blackening_history_config(
                    only_validated=only_validated
                ),
            )
            return JobDispatchResult(
                task_id="failed-cleanup-task",
                mode="celery",
                status="failed",
                video_id=video_id,
                history_id=history.pk,
            )

        with (
            patch(
                "endoreg_db.views.video.segments_crud.dispatch_video_post_validation_rebuild",
                side_effect=failed_dispatch,
            ),
            patch(
                "endoreg_db.views.video.segments_crud.record_operation",
                return_value=None,
            ),
        ):
            response = video_segments_validate_bulk(request, pk=self.video.pk)

        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 503)
        self.assertEqual(data["validation_status"], "failed")
        self.assertEqual(data["post_processing_job"]["status"], "failed")
        self.assertEqual(data["segment_annotation_status"], "cleanup_failed")
        self.assertFalse(data["segment_annotations_validated"])
        self.assertFalse(data["outside_segments_removed"])

    def test_bulk_validation_rejects_incomplete_frame_annotations(self):
        missing_frame_segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=90,
            end_frame_number=93,
            source=self.segment_source,
        )
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/validate-bulk/",
            {
                "segment_ids": [missing_frame_segment.pk],
                "segments": [
                    {
                        "id": missing_frame_segment.pk,
                        "start_time": missing_frame_segment.start_time,
                        "end_time": missing_frame_segment.end_time,
                    }
                ],
                "is_validated": True,
                "information_source_name": "manual_annotation",
            },
            format="json",
        )

        with (
            patch(
                "endoreg_db.views.video.segments_crud.dispatch_video_post_validation_rebuild",
                side_effect=AssertionError("incomplete annotations must not dispatch"),
            ),
            patch(
                "endoreg_db.views.video.segments_crud.record_operation",
                return_value=None,
            ),
        ):
            response = video_segments_validate_bulk(request, pk=self.video.pk)

        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            data["error"],
            "Segment validation did not create complete frame annotations.",
        )
        self.assertEqual(data["annotation_errors"][0]["reason"], "missing_frames")
        self.assertFalse(data["segment_annotations_validated"])
        self.assertFalse(data["outside_segments_removed"])
        state = self.video.get_or_create_state()
        state.refresh_from_db()
        self.assertFalse(state.segment_annotations_validated)


class VideoSegmentsBlackenOutsideRouteTest(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.center = Center.objects.create(name="Blacken Outside Center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="blacken-outside-video",
            original_file_name="blacken_outside.mp4",
            fps=25.0,
            frame_count=100,
        )
        self.outside_label, _ = Label.objects.get_or_create(name="outside")

    def _create_validated_outside_segment(self) -> LabelVideoSegment:
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.outside_label,
            start_frame_number=10,
            end_frame_number=20,
        )
        segment.mark_validated(
            is_validated=True,
            information_source_name="manual_annotation",
        )
        return segment

    def test_route_is_registered(self):
        match = resolve(f"/api/media/videos/{self.video.pk}/segments/blacken-outside/")

        self.assertEqual(match.url_name, "video-segments-blacken-outside")

    def test_no_outside_segments_returns_noop(self):
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/blacken-outside/",
            {"only_validated": False},
            format="json",
        )

        with patch(
            "endoreg_db.views.video.segments_crud.dispatch_video_post_validation_rebuild",
            side_effect=AssertionError("noop must not dispatch"),
        ):
            response = video_segments_blacken_outside(request, pk=self.video.pk)

        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["status"], "noop")
        self.assertEqual(data["validation_status"], "completed")
        self.assertEqual(data["outside_segment_count"], 0)
        self.assertEqual(data["video_id"], self.video.pk)
        self.assertFalse(data["only_validated"])

    def test_outside_segments_dispatch_rebuild(self):
        self._create_validated_outside_segment()
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/blacken-outside/",
            {"only_validated": True},
            format="json",
        )

        with patch(
            "endoreg_db.views.video.segments_crud.dispatch_video_post_validation_rebuild",
            return_value=JobDispatchResult(
                task_id="blacken-job",
                mode="thread",
                status="queued",
                video_id=self.video.pk,
            ),
        ) as mock_dispatch:
            response = video_segments_blacken_outside(request, pk=self.video.pk)
        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 202)
        self.assertEqual(data["status"], "queued")
        self.assertEqual(data["validation_status"], "scheduled")
        self.assertEqual(data["outside_segment_count"], 1)
        mock_dispatch.assert_called_once_with(
            video_id=self.video.pk,
            only_validated=True,
        )

    def test_unvalidated_outside_segments_are_rejected(self):
        LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.outside_label,
            start_frame_number=10,
            end_frame_number=20,
        )
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/blacken-outside/",
            {"only_validated": True},
            format="json",
        )

        response = video_segments_blacken_outside(request, pk=self.video.pk)
        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 409)
        self.assertEqual(data["status"], "validation_required")

    def test_active_non_blackening_reprocessing_returns_busy(self):
        self._create_validated_outside_segment()
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/blacken-outside/",
            {"only_validated": True},
            format="json",
        )

        with patch(
            "endoreg_db.views.video.segments_crud.dispatch_video_post_validation_rebuild",
            return_value=JobDispatchResult(
                task_id="other-reprocessing-task",
                mode="thread",
                status="busy",
                video_id=self.video.pk,
                history_id=123,
            ),
        ):
            response = video_segments_blacken_outside(request, pk=self.video.pk)

        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 409)
        self.assertEqual(data["status"], "busy")
        self.assertEqual(data["validation_status"], "running")
        self.assertEqual(data["operation"], "blacken_outside")
        self.assertEqual(data["post_processing_job"]["status"], "busy")

    def test_dispatch_error_returns_error_response(self):
        self._create_validated_outside_segment()
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/blacken-outside/",
            {"only_validated": True},
            format="json",
        )

        with patch(
            "endoreg_db.views.video.segments_crud.dispatch_video_post_validation_rebuild",
            side_effect=RuntimeError("inline rebuild failed"),
        ):
            response = video_segments_blacken_outside(request, pk=self.video.pk)
        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 500)
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["operation"], "blacken_outside")
        self.assertIn("inline rebuild failed", data["error"])

    def test_dispatch_failure_clears_stale_final_flags_and_serializes_failed(self):
        self._create_validated_outside_segment()
        state = self.video.get_or_create_state()
        state.segment_annotations_created = True
        state.segment_annotations_validated = True
        state.outside_segments_removed = True
        state.save(
            update_fields=[
                "segment_annotations_created",
                "segment_annotations_validated",
                "outside_segments_removed",
                "date_modified",
            ]
        )

        class BrokenTask:
            def apply_async(self, *args: tuple[Any], **kwargs: tuple[Any]) -> Any:
                raise RuntimeError("broker unavailable")

        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/blacken-outside/",
            {"only_validated": True},
            format="json",
        )

        with (
            patch.dict(os.environ, {"VIDEO_POST_VALIDATION_JOB_MODE": "celery"}),
            patch(
                "endoreg_db.tasks.run_video_post_validation_rebuild_task", BrokenTask()
            ),
        ):
            response = video_segments_blacken_outside(request, pk=self.video.pk)
        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 500)
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["validation_status"], "failed")
        self.assertEqual(data["post_processing_job"]["status"], "failed")
        state.refresh_from_db()
        self.assertFalse(state.segment_annotations_validated)
        self.assertFalse(state.outside_segments_removed)

        self.video.refresh_from_db()
        serializer = VideoFileListSerializer(self.video)
        serialized = _serializer_data(serializer)
        self.assertEqual(serialized["segment_annotation_status"], "cleanup_failed")
        self.assertFalse(serialized["segment_annotations_validated"])
        self.assertFalse(serialized["outside_segments_removed"])
        post_validation_rebuild = cast(
            Mapping[str, object],
            serialized["post_validation_rebuild"],
        )
        self.assertEqual(
            post_validation_rebuild["status"], VideoProcessingHistory.STATUS_FAILURE
        )

    def test_repeated_call_returns_already_queued(self):
        self._create_validated_outside_segment()
        submitted: list[Any] = []

        def fake_submit(fn: Any) -> types.SimpleNamespace:
            submitted.append(fn)
            return types.SimpleNamespace()

        with (
            patch.dict(
                os.environ,
                {"VIDEO_POST_VALIDATION_JOB_MODE": "thread"},
            ),
            patch.object(post_validation_jobs._executor, "submit", fake_submit),
        ):
            first_response = video_segments_blacken_outside(
                self.factory.post(
                    f"/api/media/videos/{self.video.pk}/segments/blacken-outside/",
                    {"only_validated": True},
                    format="json",
                ),
                pk=self.video.pk,
            )

            first_data = json.loads(first_response.content.decode())

            second_response = video_segments_blacken_outside(
                self.factory.post(
                    f"/api/media/videos/{self.video.pk}/segments/blacken-outside/",
                    {"only_validated": True},
                    format="json",
                ),
                pk=self.video.pk,
            )
            second_data = json.loads(second_response.content.decode())

        self.assertEqual(first_response.status_code, 202)
        self.assertEqual(first_data["status"], "queued")
        self.assertEqual(first_data["validation_status"], "scheduled")
        self.assertEqual(second_response.status_code, 202)
        self.assertEqual(second_data["status"], "already_queued")
        self.assertEqual(second_data["validation_status"], "scheduled")
        self.assertEqual(len(submitted), 1)

    def test_failed_cleanup_can_be_requeued(self):
        self._create_validated_outside_segment()
        failed_history = VideoProcessingHistory.objects.create(
            video=self.video,
            operation=VideoProcessingHistory.OPERATION_REPROCESSING,
            status=VideoProcessingHistory.STATUS_FAILURE,
            task_id="failed-blackening-task",
            details="broker unavailable",
            config=segment_state.blackening_history_config(only_validated=True),
        )
        submitted = []

        submitted: list[Any] = []

        def fake_submit(fn: Any) -> types.SimpleNamespace:
            submitted.append(fn)
            return types.SimpleNamespace()

        with (
            patch.dict(
                os.environ,
                {"VIDEO_POST_VALIDATION_JOB_MODE": "thread"},
            ),
            patch.object(post_validation_jobs._executor, "submit", fake_submit),
        ):
            response = video_segments_blacken_outside(
                self.factory.post(
                    f"/api/media/videos/{self.video.pk}/segments/blacken-outside/",
                    {"only_validated": True},
                    format="json",
                ),
                pk=self.video.pk,
            )
        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 202)
        self.assertEqual(data["status"], "queued")
        self.assertEqual(data["validation_status"], "scheduled")
        self.assertEqual(len(submitted), 1)
        self.assertNotEqual(
            data["post_processing_job"]["history_id"],
            failed_history.pk,
        )

    def test_only_validated_counts_only_validated_outside_segments(self):
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.outside_label,
            start_frame_number=10,
            end_frame_number=20,
        )

        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/blacken-outside/",
            {"only_validated": True},
            format="json",
        )
        with patch(
            "endoreg_db.views.video.segments_crud.dispatch_video_post_validation_rebuild",
            side_effect=AssertionError("unvalidated segment must not dispatch"),
        ):
            response = video_segments_blacken_outside(request, pk=self.video.pk)
        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 409)
        self.assertEqual(data["status"], "validation_required")
        self.assertEqual(data["validation_status"], "validation_required")

        segment.mark_validated(
            is_validated=True,
            information_source_name="manual_annotation",
        )
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/blacken-outside/",
            {"only_validated": True},
            format="json",
        )
        with patch(
            "endoreg_db.views.video.segments_crud.dispatch_video_post_validation_rebuild",
            return_value=JobDispatchResult(
                task_id="validated-blacken-job",
                mode="thread",
                status="queued",
                video_id=self.video.pk,
            ),
        ) as mock_dispatch:
            response = video_segments_blacken_outside(request, pk=self.video.pk)
        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 202)
        self.assertEqual(data["status"], "queued")
        self.assertEqual(data["validation_status"], "scheduled")
        self.assertEqual(data["outside_segment_count"], 1)
        mock_dispatch.assert_called_once_with(
            video_id=self.video.pk,
            only_validated=True,
        )


class VideoSegmentsSourceKindFilterTest(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.center = Center.objects.create(name="Segment Source Filter Center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="segment-source-filter-video",
            original_file_name="segment_filter.mp4",
            fps=25.0,
            frame_count=100,
        )
        self.label = Label.objects.create(name="segment-filter-label")
        self.manual_source = InformationSource.objects.create(name="manual_annotation")
        self.prediction_source = InformationSource.objects.create(name="prediction")

        self.manual_segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=0,
            end_frame_number=10,
            source=self.manual_source,
        )
        self.prediction_segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=20,
            end_frame_number=30,
            source=self.prediction_source,
        )

    def test_source_kind_manual_returns_only_manual_segments(self):
        request = self.factory.get(
            f"/api/media/videos/{self.video.pk}/segments/?source_kind=manual"
        )
        response = video_segments_by_video(request, pk=self.video.pk)
        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], self.manual_segment.pk)
        self.assertEqual(data[0]["segment_origin"], "manual")
        self.assertEqual(data[0]["source_name"], "manual_annotation")

    def test_source_kind_prediction_returns_only_prediction_segments(self):
        request = self.factory.get(
            f"/api/media/videos/{self.video.pk}/segments/?source_kind=prediction"
        )
        response = video_segments_by_video(request, pk=self.video.pk)

        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], self.prediction_segment.pk)
        self.assertEqual(data[0]["segment_origin"], "prediction")
        self.assertEqual(data[0]["source_name"], "prediction")


class VideoSegmentsBulkMutationTest(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.center = Center.objects.create(name="Bulk Segment Center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="bulk-segments-video",
            original_file_name="bulk_segments.mp4",
            fps=25.0,
            frame_count=200,
        )
        self.label_a = Label.objects.create(name="bulk-outside")
        self.label_b = Label.objects.create(name="bulk-polyp")
        self.manual_source = InformationSource.objects.create(name="manual_annotation")
        self.update_segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label_a,
            start_frame_number=10,
            end_frame_number=20,
            source=self.manual_source,
        )
        self.delete_segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label_b,
            start_frame_number=30,
            end_frame_number=40,
            source=self.manual_source,
        )
        self.frames = [
            Frame.objects.create(
                video=self.video,
                frame_number=frame_number,
                relative_path=f"frame_{frame_number:07d}.jpg",
            )
            for frame_number in [*range(10, 20), *range(30, 40)]
        ]
        ImageClassificationAnnotation.objects.bulk_create(
            [
                ImageClassificationAnnotation(
                    frame=frame,
                    label=self.label_a if frame.frame_number < 20 else self.label_b,
                    value=True,
                    information_source=self.manual_source,
                )
                for frame in self.frames
            ]
        )
        state = self.video.get_or_create_state()
        state.segment_annotations_created = True
        state.segment_annotations_validated = True
        state.save(
            update_fields=[
                "segment_annotations_created",
                "segment_annotations_validated",
            ]
        )

    def test_bulk_mutation_defers_annotation_sync_and_marks_state_stale(self):
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/bulk/",
            {
                "defer_annotation_sync": True,
                "creates": [
                    {
                        "client_id": -1,
                        "label_id": self.label_a.pk,
                        "start_time": 2.0,
                        "end_time": 3.0,
                        "export_segment": True,
                    }
                ],
                "updates": [
                    {
                        "id": self.update_segment.pk,
                        "label_id": self.label_b.pk,
                        "start_time": 1.0,
                        "end_time": 1.5,
                        "export_segment": True,
                    }
                ],
                "deletes": [self.delete_segment.pk],
            },
            format="json",
        )

        with patch(
            "endoreg_db.views.video.segments_crud._sync_frame_annotations"
        ) as sync:
            response = video_segments_bulk_mutation(request, pk=self.video.pk)

        data = json.loads(response.content.decode())
        self.assertEqual(response.status_code, 200)
        sync.assert_not_called()
        self.assertEqual(data["created_count"], 1)
        self.assertEqual(data["updated_count"], 1)
        self.assertEqual(data["deleted_count"], 1)
        self.assertEqual(data["created"][0]["client_id"], -1)
        self.assertEqual(data["created"][0]["segment"]["label_id"], self.label_a.pk)
        self.assertEqual(data["deleted"], [self.delete_segment.pk])

        created_id = data["created"][0]["segment"]["id"]
        created_segment = LabelVideoSegment.objects.get(pk=created_id)
        self.assertEqual(created_segment.start_frame_number, 50)
        self.assertEqual(created_segment.end_frame_number, 75)
        self.assertTrue(created_segment.export_segment)

        self.update_segment.refresh_from_db()
        self.assertEqual(getattr(self.update_segment, "label_id"), self.label_b.pk)
        self.assertEqual(self.update_segment.start_frame_number, 25)
        self.assertEqual(self.update_segment.end_frame_number, 38)
        self.assertTrue(self.update_segment.export_segment)
        self.assertFalse(
            LabelVideoSegment.objects.filter(pk=self.delete_segment.pk).exists()
        )
        self.assertEqual(
            ImageClassificationAnnotation.objects.filter(
                frame__video=self.video,
                frame__frame_number__gte=10,
                frame__frame_number__lt=20,
                label=self.label_a,
                information_source=self.manual_source,
            ).count(),
            0,
        )
        self.assertEqual(
            ImageClassificationAnnotation.objects.filter(
                frame__video=self.video,
                frame__frame_number__gte=30,
                frame__frame_number__lt=40,
                label=self.label_b,
                information_source=self.manual_source,
            ).count(),
            0,
        )

        self.video.refresh_from_db()
        state = self.video.get_or_create_state()
        self.assertFalse(state.segment_annotations_created)
        self.assertFalse(state.segment_annotations_validated)

    def test_bulk_mutation_attaches_created_and_updated_segments_to_ai_dataset(self):
        dataset = AIDataSet.objects.create(
            name="bulk-segment-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_VIDEO,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_VIDEO_SEGMENT_CLASSIFICATION,
        )
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/bulk/",
            {
                "ai_dataset_id": dataset.pk,
                "defer_annotation_sync": True,
                "creates": [
                    {
                        "client_id": -1,
                        "label_id": self.label_a.pk,
                        "start_time": 2.0,
                        "end_time": 3.0,
                    }
                ],
                "updates": [
                    {
                        "id": self.update_segment.pk,
                        "label_id": self.label_b.pk,
                        "start_time": 1.0,
                        "end_time": 1.5,
                    }
                ],
                "deletes": [],
            },
            format="json",
        )

        response = video_segments_bulk_mutation(request, pk=self.video.pk)
        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["ai_dataset_id"], dataset.pk)
        created_id = data["created"][0]["segment"]["id"]
        self.assertEqual(
            data["attached_segment_ids"],
            sorted([created_id, self.update_segment.pk]),
        )
        self.assertTrue(dataset.video_annotations.filter(pk=created_id).exists())
        self.assertTrue(
            dataset.video_annotations.filter(pk=self.update_segment.pk).exists()
        )

    def test_bulk_mutation_rolls_back_on_invalid_update(self):
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/bulk/",
            {
                "defer_annotation_sync": True,
                "creates": [
                    {
                        "client_id": -1,
                        "label_id": self.label_a.pk,
                        "start_time": 4.0,
                        "end_time": 5.0,
                    }
                ],
                "updates": [
                    {
                        "id": self.update_segment.pk,
                        "start_time": 2.0,
                        "end_time": 1.0,
                    }
                ],
                "deletes": [],
            },
            format="json",
        )

        response = video_segments_bulk_mutation(request, pk=self.video.pk)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            LabelVideoSegment.objects.filter(
                video_file=self.video,
                start_frame_number=100,
                end_frame_number=125,
            ).count(),
            0,
        )
        self.update_segment.refresh_from_db()
        self.assertEqual(self.update_segment.start_frame_number, 10)
        self.assertEqual(self.update_segment.end_frame_number, 20)


class ImportPredictionSegmentsToManualTest(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.center = Center.objects.create(name="Prediction Import Center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="prediction-import-video",
            original_file_name="prediction_import.mp4",
            fps=25.0,
            frame_count=200,
        )
        self.label_a = Label.objects.create(name="outside")
        self.label_b = Label.objects.create(name="polyp")
        self.manual_source = InformationSource.objects.create(name="manual_annotation")
        self.prediction_source = InformationSource.objects.create(name="prediction")

        self.existing_manual_segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label_a,
            start_frame_number=1,
            end_frame_number=5,
            source=self.manual_source,
        )
        self.prediction_segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label_b,
            start_frame_number=10,
            end_frame_number=20,
            source=self.prediction_source,
        )

    def test_import_replaces_manual_segments_but_keeps_prediction_segments(self):
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/import-predictions/",
            {
                "replace_existing": True,
                "segments": [
                    {
                        "label_name": "outside",
                        "start_time": 2.0,
                        "end_time": 4.0,
                    },
                    {
                        "label_name": "polyp",
                        "start_time": 5.0,
                        "end_time": 7.0,
                        "export_segment": True,
                    },
                ],
            },
            format="json",
        )

        response = import_prediction_segments_to_manual(request, pk=self.video.pk)
        data = json.loads(response.content.decode())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["created_count"], 2)
        self.assertTrue(data["replaced_existing"])
        self.assertEqual(
            [segment["segment_origin"] for segment in data["segments"]],
            ["manual", "manual"],
        )

        persisted_manual_segments = LabelVideoSegment.objects.filter(
            video_file=self.video,
            source__name="manual_annotation",
        ).order_by("start_frame_number")
        self.assertEqual(persisted_manual_segments.count(), 2)
        self.assertFalse(
            persisted_manual_segments.filter(
                pk=self.existing_manual_segment.pk
            ).exists()
        )

        self.assertTrue(
            LabelVideoSegment.objects.filter(pk=self.prediction_segment.pk).exists()
        )

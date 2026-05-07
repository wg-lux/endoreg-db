import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIRequestFactory

from endoreg_db.models import (
    AIDataSet,
    AiModel,
    Center,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    LabelSet,
    LabelVideoSegment,
    ModelMeta,
    VideoFile,
    VideoPredictionMeta,
)
from endoreg_db.services.segment_annotations import (
    ensure_prediction_segment_annotations,
)
from endoreg_db.services.video_temporal_inference import (
    TemporalInferenceDispatchResult,
)
from endoreg_db.views.video.ai import (
    FrameAnnotationBulkUpsertView,
    FrameAnnotationRandomTaskView,
    prediction_model_list,
    rerun_prediction_segments,
)


class FrameAnnotationPipe1WorkflowIntegrationTest(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.random_task_view = FrameAnnotationRandomTaskView.as_view()
        self.bulk_upsert_view = FrameAnnotationBulkUpsertView.as_view()

        self.center = Center.objects.create(name="pipe-1-frame-center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="pipe-1-frame-video",
            original_file_name="pipe_1_frame_video.mp4",
            fps=25.0,
            frame_count=30,
        )
        self.frame_dir_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.frame_dir_temp.cleanup)
        self.video.frame_dir = self.frame_dir_temp.name
        self.video.save(update_fields=["frame_dir"])
        self.frames = [
            Frame.objects.create(
                video=self.video,
                frame_number=frame_number,
                relative_path=f"frame_{frame_number:07d}.jpg",
                is_extracted=True,
            )
            for frame_number in (10, 11, 12)
        ]
        frame_dir = Path(self.frame_dir_temp.name)
        for frame in self.frames:
            (frame_dir / frame.relative_path).write_bytes(b"frame")

        self.manual_source = InformationSource.objects.create(name="manual_annotation")
        self.predicted_label = Label.objects.create(name="pipe-1-polyp")
        self.other_label = Label.objects.create(name="pipe-1-outside")
        self.label_set = LabelSet.objects.create(
            name="pipe-1-frame-label-group",
            version=1,
        )
        self.label_set.labels.add(self.predicted_label, self.other_label)

        self.ai_model = AiModel.objects.create(name="pipe-1-frame-model")
        self.model_meta = ModelMeta.objects.create(
            name="pipe-1-frame-model-meta",
            version="1",
            model=self.ai_model,
            labelset=self.label_set,
        )
        self.ai_model.active_meta = self.model_meta
        self.ai_model.save(update_fields=["active_meta"])

        self.dataset = AIDataSet.objects.create(
            name="pipe-1-frame-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_VIDEO,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_VIDEO_SEGMENT_CLASSIFICATION,
        )

    def _mark_frames_extracted(self, video_file, *args, **kwargs):
        state = video_file.get_or_create_state()
        state.frames_extracted = True
        state.frames_initialized = True
        state.frame_count = len(self.frames)
        state.save(
            update_fields=["frames_extracted", "frames_initialized", "frame_count"]
        )

    def _mark_text_metadata_extracted(self, video_file, *args, **kwargs):
        state = video_file.get_or_create_state()
        state.text_meta_extracted = True
        state.save(update_fields=["text_meta_extracted"])

    def _predict_pipe_1_sequences(self, video_file, *, model_meta, **kwargs):
        VideoPredictionMeta.objects.get_or_create(
            video_file=video_file,
            model_meta=model_meta,
        )
        return {self.predicted_label.name: [(10, 13)]}

    def _run_pipe_1_without_io(self) -> LabelVideoSegment:
        with (
            patch.object(VideoFile, "update_video_meta", autospec=True),
            patch.object(
                VideoFile,
                "extract_frames",
                autospec=True,
                side_effect=self._mark_frames_extracted,
            ),
            patch.object(
                VideoFile,
                "update_text_metadata",
                autospec=True,
                side_effect=self._mark_text_metadata_extracted,
            ),
            patch.object(
                VideoFile,
                "predict_video",
                autospec=True,
                side_effect=self._predict_pipe_1_sequences,
            ),
        ):
            success = self.video.pipe_1(
                model_name=self.ai_model.name,
                delete_frames_after=False,
                ocr_frame_fraction=0.01,
                ocr_cap=1,
            )

        self.assertTrue(success)
        self.video.refresh_from_db()
        state = self.video.get_or_create_state()
        self.assertTrue(state.frames_extracted)
        self.assertTrue(state.text_meta_extracted)
        self.assertTrue(state.initial_prediction_completed)
        self.assertTrue(state.lvs_created)

        prediction_meta = VideoPredictionMeta.objects.get(
            video_file=self.video,
            model_meta=self.model_meta,
        )
        segment = LabelVideoSegment.objects.get(
            video_file=self.video,
            label=self.predicted_label,
            prediction_meta=prediction_meta,
        )
        self.assertIsNotNone(segment.source)
        source = segment.source
        assert source is not None
        self.assertEqual(source.name, "prediction")
        self.assertEqual(segment.start_frame_number, 10)
        self.assertEqual(segment.end_frame_number, 13)

        self.dataset.video_annotations.add(segment)
        return segment

    def _materialize_pipe_1_prediction_annotations(self) -> LabelVideoSegment:
        segment = self._run_pipe_1_without_io()

        stats = ensure_prediction_segment_annotations(
            video_ids=[self.video.pk],
            information_source_name="prediction_annotation",
            commit=True,
        )

        self.assertEqual(stats["eligible_prediction_segments"], 1)
        self.assertEqual(stats["segments_processed"], 1)
        self.assertEqual(stats["annotations_created"], len(self.frames))
        return segment

    def _load_pipe_1_frame_task(self, *, exclude_annotated: bool = False):
        request = self.factory.get(
            "/api/media/annotations/frames/random-task/",
            {
                "video_id": self.video.pk,
                "label_group_id": self.label_set.pk,
                "target_label": self.predicted_label.name,
                "limit": 1,
                "ai_dataset_name": self.dataset.name,
                "ai_dataset_type": self.dataset.dataset_type,
                "dataset_frame_filter": "segments",
                "prediction_segments_only": "true",
                "information_source_name": self.manual_source.name,
                "annotator": "alice",
                "exclude_annotated": "true" if exclude_annotated else "false",
            },
        )
        with patch(
            "endoreg_db.models.state.frame_annotation.random.randint",
            return_value=0,
        ):
            return self.random_task_view(request)

    def test_pipe_1_predictions_feed_frame_annotation_tasks(self):
        self._materialize_pipe_1_prediction_annotations()

        response = self._load_pipe_1_frame_task()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["selection_strategy"], "dataset_segments")
        self.assertEqual(
            response.data["segment_bucket_counts"], {str(self.predicted_label.pk): 3}
        )
        self.assertEqual(
            response.data["selected_label_counts"], {str(self.predicted_label.pk): 1}
        )

        task = response.data["task"]
        self.assertEqual(task["frame_id"], self.frames[0].pk)
        self.assertEqual(task["dataset_selection_label_id"], self.predicted_label.pk)
        self.assertEqual(task["suggested_label_ids"], [self.predicted_label.pk])
        self.assertEqual(
            {label["id"] for label in task["label_options"]},
            {self.predicted_label.pk, self.other_label.pk},
        )

        prediction_annotations = task["prediction_annotations"]
        self.assertEqual(len(prediction_annotations), 1)
        self.assertEqual(prediction_annotations[0]["label_id"], self.predicted_label.pk)
        self.assertEqual(
            prediction_annotations[0]["information_source_name"],
            "prediction_annotation",
        )

    def test_prediction_model_list_exposes_local_and_huggingface_options(self):
        request = self.factory.get("/api/media/videos/prediction-models/list/")

        response = prediction_model_list(request)

        self.assertEqual(response.status_code, 200, response.data)
        model_ids = {model["id"] for model in response.data["models"]}
        self.assertIn(self.model_meta.pk, model_ids)
        self.assertEqual(
            response.data["default_huggingface_model_id"],
            "wg-lux/colo_segmentation_RegNetX800MF_base",
        )
        self.assertEqual(
            response.data["default_labelset_name"],
            "multilabel_classification_colonoscopy_default",
        )
        self.assertEqual(
            response.data["huggingface_models"][0]["model_id"],
            "wg-lux/colo_segmentation_RegNetX800MF_base",
        )

    def test_rerun_prediction_segments_dispatches_temporal_inference_job(
        self,
    ):
        prediction_source = InformationSource.objects.create(name="prediction")
        old_prediction_meta = VideoPredictionMeta.objects.create(
            video_file=self.video,
            model_meta=self.model_meta,
        )
        old_segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.other_label,
            start_frame_number=1,
            end_frame_number=3,
            source=prediction_source,
            prediction_meta=old_prediction_meta,
        )

        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/rerun-predictions/",
            {
                "model_meta_id": self.model_meta.pk,
                "temporal_model": "markov",
                "delete_frames_after": False,
            },
            format="json",
        )

        with patch(
            "endoreg_db.views.video.ai.label.dispatch_video_temporal_inference",
            return_value=TemporalInferenceDispatchResult(
                task_id="temporal-task",
                mode="celery",
                status="queued",
                video_id=self.video.pk,
                model_meta_id=self.model_meta.pk,
                queue="inference",
                history_id=123,
            ),
        ) as dispatch:
            response = rerun_prediction_segments(request, self.video.pk)

        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data["status"], "queued")
        self.assertEqual(response.data["job"]["task_id"], "temporal-task")
        self.assertEqual(response.data["prediction_segments_count"], 1)
        self.assertEqual(response.data["model_meta"]["id"], self.model_meta.pk)
        self.assertTrue(LabelVideoSegment.objects.filter(pk=old_segment.pk).exists())
        dispatch.assert_called_once_with(
            video_id=self.video.pk,
            model_meta_id=self.model_meta.pk,
            replace_prediction_segments=True,
            delete_frames_after=False,
            ocr_frame_fraction=0.001,
            ocr_cap=10,
            temporal_options={"temporal_model": "markov"},
            test_run=False,
            n_test_frames=10,
        )

    def test_rerun_prediction_segments_reports_inline_completion(self):
        request = self.factory.post(
            f"/api/media/videos/{self.video.pk}/segments/rerun-predictions/",
            {"model_meta_id": self.model_meta.pk},
            format="json",
        )

        with patch(
            "endoreg_db.views.video.ai.label.dispatch_video_temporal_inference",
            return_value=TemporalInferenceDispatchResult(
                task_id="temporal-inline-task",
                mode="inline",
                status="completed",
                video_id=self.video.pk,
                model_meta_id=self.model_meta.pk,
                queue="inference",
                history_id=456,
                deleted_prediction_segments=2,
                prediction_segments_count=3,
            ),
        ):
            response = rerun_prediction_segments(request, self.video.pk)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(response.data["deleted_prediction_segments"], 2)
        self.assertEqual(response.data["prediction_segments_count"], 3)

    def test_manual_frame_annotation_after_pipe_1_excludes_completed_target(self):
        self._materialize_pipe_1_prediction_annotations()
        first_response = self._load_pipe_1_frame_task()
        self.assertEqual(first_response.status_code, 200, first_response.data)
        first_task = first_response.data["task"]

        annotations = [
            {
                "frame_id": first_task["frame_id"],
                "label_id": label["id"],
                "information_source_name": self.manual_source.name,
                "annotator": "alice",
                "value": label["id"] == self.predicted_label.pk,
                "external_annotation_id": f"manual-{first_task['frame_id']}-{label['id']}",
            }
            for label in first_task["label_options"]
        ]
        request = self.factory.post(
            "/api/media/annotations/frames/bulk-upsert/",
            {"video_id": self.video.pk, "annotations": annotations},
            format="json",
        )

        upsert_response = self.bulk_upsert_view(request)

        self.assertEqual(upsert_response.status_code, 200, upsert_response.data)
        self.assertEqual(upsert_response.data["upserted_count"], len(annotations))
        self.assertTrue(
            ImageClassificationAnnotation.objects.filter(
                frame_id=first_task["frame_id"],
                label=self.predicted_label,
                information_source=self.manual_source,
                annotator="alice",
                value=True,
            ).exists()
        )

        next_response = self._load_pipe_1_frame_task(exclude_annotated=True)

        self.assertEqual(next_response.status_code, 200, next_response.data)
        next_task = next_response.data["task"]
        self.assertNotEqual(next_task["frame_id"], first_task["frame_id"])
        self.assertEqual(next_task["frame_id"], self.frames[1].pk)

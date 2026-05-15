from django.test import TestCase
from rest_framework.test import APIRequestFactory
from unittest.mock import patch

from endoreg_db.models import (
    AIDataSet,
    Center,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    LabelVideoSegment,
    LabelSet,
    VideoFile,
)
from endoreg_db.views.video.ai import (
    FrameAnnotationBulkUpsertView,
    FrameAnnotationRandomTaskView,
    FrameAnnotationSkipView,
    label_set_list,
)


class FrameAnnotationTaskEndpointsTest(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.bulk_upsert_view = FrameAnnotationBulkUpsertView.as_view()
        self.random_task_view = FrameAnnotationRandomTaskView.as_view()
        self.skip_view = FrameAnnotationSkipView.as_view()

        self.center = Center.objects.create(name="frame-task-center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="frame-task-video-hash",
            original_file_name="frame_task.mp4",
            fps=25.0,
            frame_count=100,
        )
        self.frame_1 = Frame.objects.create(
            video=self.video,
            frame_number=10,
            relative_path="frame_0000010.jpg",
            is_extracted=True,
        )
        self.frame_2 = Frame.objects.create(
            video=self.video,
            frame_number=11,
            relative_path="frame_0000011.jpg",
            is_extracted=True,
        )
        self.frame_3 = Frame.objects.create(
            video=self.video,
            frame_number=12,
            relative_path="frame_0000012.jpg",
            is_extracted=True,
        )
        self.source = InformationSource.objects.create(name="manual_annotation")
        self.prediction_source = InformationSource.objects.create(
            name="task-prediction-source"
        )
        self.label = Label.objects.create(name="task-test-label")
        self.target_label = Label.objects.create(name="task-target-label")
        self.filter_label = Label.objects.create(name="task-filter-label")
        self.unrelated_label = Label.objects.create(name="task-unrelated-label")
        self.label_set = LabelSet.objects.create(
            name="frame-task-label-group",
            version=1,
        )
        self.label_set.labels.add(self.target_label, self.filter_label, self.label)

    def test_label_set_list_returns_frame_annotation_label_groups(self):
        request = self.factory.get("/api/media/videos/label-sets/list/")

        response = label_set_list(request)

        self.assertEqual(response.status_code, 200)
        label_groups = list(response.data)
        label_group = next(
            group for group in label_groups if group["id"] == self.label_set.pk
        )
        self.assertEqual(label_group["name"], self.label_set.name)
        self.assertEqual(label_group["version"], self.label_set.version)
        self.assertEqual(label_group["label_count"], 3)
        self.assertEqual(
            [label["name"] for label in label_group["labels"]],
            sorted([self.target_label.name, self.filter_label.name, self.label.name]),
        )

    def test_random_task_returns_available_frame(self):
        request = self.factory.get(
            "/api/media/annotations/frames/random-task/",
            {
                "video_id": self.video.pk,
                "information_source_name": self.source.name,
            },
        )

        with patch(
            "endoreg_db.models.state.frame_annotation.random.randint",
            return_value=0,
        ):
            response = self.random_task_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "success")
        self.assertIn(
            response.data["task"]["frame_id"], {self.frame_1.pk, self.frame_2.pk}
        )
        self.assertEqual(response.data["task"]["video_id"], self.video.pk)

    def test_random_task_404_when_no_unannotated_frame_left(self):
        ImageClassificationAnnotation.objects.create(
            frame=self.frame_1,
            label=self.label,
            value=True,
            information_source=self.source,
            annotator="alice",
        )
        ImageClassificationAnnotation.objects.create(
            frame=self.frame_2,
            label=self.label,
            value=True,
            information_source=self.source,
            annotator="alice",
        )
        ImageClassificationAnnotation.objects.create(
            frame=self.frame_3,
            label=self.label,
            value=True,
            information_source=self.source,
            annotator="alice",
        )

        request = self.factory.get(
            "/api/media/annotations/frames/random-task/",
            {
                "video_id": self.video.pk,
                "information_source_name": self.source.name,
                "annotator": "alice",
                "exclude_annotated": "true",
            },
        )

        with patch(
            "endoreg_db.models.state.frame_annotation.random.randint",
            return_value=0,
        ):
            response = self.random_task_view(request)

        self.assertEqual(response.status_code, 404)

    def test_skip_acknowledges_and_returns_next_task(self):
        request = self.factory.post(
            "/api/media/annotations/frames/skip/",
            {
                "frame_id": self.frame_1.pk,
                "video_id": self.video.pk,
                "information_source_name": self.source.name,
                "annotator": "alice",
                "reason": "blurred frame",
            },
            format="json",
        )

        response = self.skip_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "success")
        self.assertEqual(response.data["skipped_frame_id"], self.frame_1.pk)
        self.assertEqual(response.data["video_id"], self.video.pk)
        self.assertEqual(response.data["annotator"], "alice")
        self.assertIn("next_task", response.data)
        self.assertNotEqual(
            response.data["next_task"]["frame_id"],
            self.frame_1.pk,
        )

    def test_skip_rejects_unknown_frame(self):
        request = self.factory.post(
            "/api/media/annotations/frames/skip/",
            {
                "frame_id": 999999,
                "video_id": self.video.pk,
            },
            format="json",
        )

        response = self.skip_view(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "Unknown frame_id.")

    def test_bulk_upsert_attaches_annotations_to_exact_ai_dataset(self):
        dataset = AIDataSet.objects.create(
            name="bulk-upsert-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        other_dataset = AIDataSet.objects.create(
            name=dataset.name,
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        request = self.factory.post(
            "/api/media/annotations/frames/bulk-upsert/",
            {
                "video_id": self.video.pk,
                "ai_dataset_id": dataset.pk,
                "annotations": [
                    {
                        "frame_id": self.frame_1.pk,
                        "label_id": self.label.pk,
                        "value": True,
                        "information_source_name": self.source.name,
                        "annotator": "alice",
                    }
                ],
            },
            format="json",
        )

        response = self.bulk_upsert_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["ai_dataset_id"], dataset.pk)
        annotation = ImageClassificationAnnotation.objects.get(
            frame=self.frame_1,
            label=self.label,
            information_source=self.source,
            annotator="alice",
        )
        self.assertEqual(
            response.data["attached_frame_annotation_ids"], [annotation.pk]
        )
        self.assertTrue(dataset.image_annotations.filter(pk=annotation.pk).exists())
        self.assertFalse(
            other_dataset.image_annotations.filter(pk=annotation.pk).exists()
        )

    def test_random_task_filtered_mode_requires_filter_label(self):
        request = self.factory.get(
            "/api/media/annotations/frames/random-task/",
            {
                "video_id": self.video.pk,
                "task_mode": "filtered",
                "target_label": self.target_label.name,
            },
        )

        with patch(
            "endoreg_db.models.state.frame_annotation.random.randint",
            return_value=0,
        ):
            response = self.random_task_view(request)

        self.assertEqual(response.status_code, 400)
        self.assertIn("filter_label", response.data["error"])

    def test_random_task_filtered_mode_returns_only_matching_frames(self):
        ImageClassificationAnnotation.objects.create(
            frame=self.frame_1,
            label=self.filter_label,
            value=True,
            information_source=self.prediction_source,
            annotator="model",
        )

        request = self.factory.get(
            "/api/media/annotations/frames/random-task/",
            {
                "video_id": self.video.pk,
                "task_mode": "filtered",
                "filter_label": self.filter_label.name,
                "target_label": self.target_label.name,
                "information_source_name": self.source.name,
                "annotator": "alice",
            },
        )

        response = self.random_task_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "success")
        self.assertEqual(response.data["task_mode"], "filtered")
        self.assertEqual(response.data["task"]["frame_id"], self.frame_1.pk)
        self.assertEqual(response.data["count"], 1)

    def test_random_task_filtered_mode_excludes_target_already_annotated(self):
        ImageClassificationAnnotation.objects.create(
            frame=self.frame_1,
            label=self.filter_label,
            value=True,
            information_source=self.prediction_source,
            annotator="model",
        )
        ImageClassificationAnnotation.objects.create(
            frame=self.frame_2,
            label=self.filter_label,
            value=True,
            information_source=self.prediction_source,
            annotator="model",
        )
        ImageClassificationAnnotation.objects.create(
            frame=self.frame_1,
            label=self.target_label,
            value=True,
            information_source=self.source,
            annotator="alice",
        )

        request = self.factory.get(
            "/api/media/annotations/frames/random-task/",
            {
                "video_id": self.video.pk,
                "task_mode": "filtered",
                "filter_label": self.filter_label.name,
                "target_label": self.target_label.name,
                "information_source_name": self.source.name,
                "annotator": "alice",
                "exclude_annotated": "true",
            },
        )

        response = self.random_task_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["task"]["frame_id"], self.frame_2.pk)

    def test_random_task_uses_previous_label_alias(self):
        ImageClassificationAnnotation.objects.create(
            frame=self.frame_3,
            label=self.filter_label,
            value=True,
            information_source=self.prediction_source,
            annotator="model",
        )

        request = self.factory.get(
            "/api/media/annotations/frames/random-task/",
            {
                "video_id": self.video.pk,
                "task_mode": "filtered",
                "previous_label": self.filter_label.name,
                "target_label": self.target_label.name,
            },
        )

        response = self.random_task_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["task"]["frame_id"], self.frame_3.pk)

    def test_random_task_label_group_restricts_label_lookup(self):
        request = self.factory.get(
            "/api/media/annotations/frames/random-task/",
            {
                "video_id": self.video.pk,
                "task_mode": "filtered",
                "label_group_id": self.label_set.pk,
                "filter_label": self.unrelated_label.name,
                "target_label": self.target_label.name,
            },
        )

        response = self.random_task_view(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "Unknown filter_label.")

    def test_random_task_limit_returns_multiple_tasks(self):
        request = self.factory.get(
            "/api/media/annotations/frames/random-task/",
            {
                "video_id": self.video.pk,
                "limit": 2,
                "information_source_name": self.source.name,
                "exclude_annotated": "false",
            },
        )

        response = self.random_task_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "success")
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(len(response.data["tasks"]), 2)
        self.assertIn("task", response.data)

    def test_random_task_serializes_multilabel_prediction_and_manual_state(self):
        self.prediction_source.name = "prediction_annotation"
        self.prediction_source.save(update_fields=["name"])
        ImageClassificationAnnotation.objects.create(
            frame=self.frame_1,
            label=self.target_label,
            value=True,
            float_value=0.91,
            information_source=self.prediction_source,
            annotator="pipe-1",
        )
        ImageClassificationAnnotation.objects.create(
            frame=self.frame_1,
            label=self.filter_label,
            value=False,
            information_source=self.source,
            annotator="alice",
            external_annotation_id="manual-false",
        )
        ImageClassificationAnnotation.objects.create(
            frame=self.frame_1,
            label=self.label,
            value=True,
            information_source=self.source,
            annotator="alice",
            external_annotation_id="manual-true",
        )

        request = self.factory.get(
            "/api/media/annotations/frames/random-task/",
            {
                "video_id": self.video.pk,
                "label_group_id": self.label_set.pk,
                "information_source_name": self.source.name,
                "annotator": "alice",
                "exclude_annotated": "false",
            },
        )

        with patch(
            "endoreg_db.models.state.frame_annotation.random.randint",
            return_value=0,
        ):
            response = self.random_task_view(request)

        self.assertEqual(response.status_code, 200)
        task = response.data["task"]
        self.assertEqual(task["annotation_mode"], "multilabel")
        self.assertEqual(
            {label["id"] for label in task["label_options"]},
            {self.target_label.pk, self.filter_label.pk, self.label.pk},
        )
        self.assertEqual(task["manual_positive_label_ids"], [self.label.pk])
        self.assertEqual(task["prediction_positive_label_ids"], [self.target_label.pk])
        self.assertEqual(task["suggested_label_ids"], [self.label.pk])
        self.assertEqual(len(task["manual_annotations"]), 2)
        self.assertEqual(len(task["prediction_annotations"]), 1)

    def test_random_task_balances_frames_from_ai_dataset_buckets(self):
        dataset = AIDataSet.objects.create(
            name="frame-task-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        positive_annotation = ImageClassificationAnnotation.objects.create(
            frame=self.frame_1,
            label=self.target_label,
            value=True,
            information_source=self.source,
            annotator="dataset",
        )
        negative_annotation = ImageClassificationAnnotation.objects.create(
            frame=self.frame_2,
            label=self.target_label,
            value=False,
            information_source=self.source,
            annotator="dataset",
        )
        unknown_annotation = ImageClassificationAnnotation.objects.create(
            frame=self.frame_3,
            label=self.label,
            value=True,
            information_source=self.source,
            annotator="dataset",
        )
        dataset.image_annotations.add(
            positive_annotation,
            negative_annotation,
            unknown_annotation,
        )

        request = self.factory.get(
            "/api/media/annotations/frames/random-task/",
            {
                "video_id": self.video.pk,
                "label_group_id": self.label_set.pk,
                "target_label": self.target_label.name,
                "limit": 3,
                "ai_dataset_name": dataset.name,
                "ai_dataset_type": dataset.dataset_type,
                "exclude_annotated": "false",
            },
        )

        with patch(
            "endoreg_db.models.state.frame_annotation.random.randint",
            return_value=0,
        ):
            response = self.random_task_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["ai_dataset_name"], dataset.name)
        self.assertEqual(response.data["ai_dataset_type"], dataset.dataset_type)
        self.assertEqual(
            response.data["bucket_counts"],
            {"positive": 1, "negative": 1, "unknown": 1},
        )
        self.assertEqual(response.data["count"], 3)
        self.assertEqual(
            [task["dataset_bucket"] for task in response.data["tasks"]],
            ["positive", "negative", "unknown"],
        )

    def test_random_task_uses_ai_dataset_segment_distribution_filter(self):
        dataset = AIDataSet.objects.create(
            name="frame-task-video-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_VIDEO,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_VIDEO_SEGMENT_CLASSIFICATION,
        )
        target_annotation_1 = ImageClassificationAnnotation.objects.create(
            frame=self.frame_1,
            label=self.target_label,
            value=True,
            information_source=self.source,
            annotator="dataset",
        )
        target_annotation_2 = ImageClassificationAnnotation.objects.create(
            frame=self.frame_2,
            label=self.target_label,
            value=True,
            information_source=self.source,
            annotator="dataset",
        )
        prediction_source = InformationSource.objects.create(
            name="prediction_annotation"
        )
        prediction_segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            source=prediction_source,
            start_frame_number=self.frame_3.frame_number,
            end_frame_number=self.frame_3.frame_number + 1,
        )
        dataset.image_annotations.add(target_annotation_1, target_annotation_2)
        dataset.video_annotations.add(prediction_segment)

        request = self.factory.get(
            "/api/media/annotations/frames/random-task/",
            {
                "video_id": self.video.pk,
                "label_group_id": self.label_set.pk,
                "target_label": self.target_label.name,
                "limit": 1,
                "ai_dataset_name": dataset.name,
                "ai_dataset_type": dataset.dataset_type,
                "dataset_frame_filter": "segments",
                "prediction_segments_only": "true",
                "exclude_annotated": "false",
            },
        )

        with patch(
            "endoreg_db.models.state.frame_annotation.random.randint",
            return_value=0,
        ):
            response = self.random_task_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["ai_dataset_name"], dataset.name)
        self.assertEqual(response.data["ai_dataset_type"], dataset.dataset_type)
        self.assertEqual(response.data["selection_strategy"], "dataset_segments")

    def test_random_task_uses_explicit_ai_dataset_id(self):
        stale_dataset = AIDataSet.objects.create(
            name="duplicate-frame-task-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        selected_dataset = AIDataSet.objects.create(
            name=stale_dataset.name,
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        stale_annotation = ImageClassificationAnnotation.objects.create(
            frame=self.frame_1,
            label=self.target_label,
            value=True,
            information_source=self.source,
            annotator="dataset",
        )
        selected_annotation = ImageClassificationAnnotation.objects.create(
            frame=self.frame_2,
            label=self.target_label,
            value=True,
            information_source=self.source,
            annotator="dataset",
        )
        stale_dataset.image_annotations.add(stale_annotation)
        selected_dataset.image_annotations.add(selected_annotation)

        request = self.factory.get(
            "/api/media/annotations/frames/random-task/",
            {
                "video_id": self.video.pk,
                "label_group_id": self.label_set.pk,
                "target_label": self.target_label.name,
                "limit": 1,
                "ai_dataset_id": selected_dataset.pk,
                "ai_dataset_name": stale_dataset.name,
                "ai_dataset_type": stale_dataset.dataset_type,
                "exclude_annotated": "false",
            },
        )

        with patch(
            "endoreg_db.models.state.frame_annotation.random.randint",
            return_value=0,
        ):
            response = self.random_task_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["ai_dataset_id"], selected_dataset.pk)
        self.assertEqual(response.data["task"]["frame_id"], self.frame_2.pk)

    def test_random_task_phi_dataset_reports_raw_video_requirement_when_no_raw_frame_matches(
        self,
    ):
        dataset = AIDataSet.objects.create(
            name="frame-task-phi-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type="phi_region_detector",
        )
        dataset.image_annotations.add(
            ImageClassificationAnnotation.objects.create(
                frame=self.frame_1,
                label=self.target_label,
                value=True,
                information_source=self.source,
                annotator="dataset",
            )
        )

        request = self.factory.get(
            "/api/media/annotations/frames/random-task/",
            {
                "video_id": self.video.pk,
                "ai_dataset_name": dataset.name,
                "ai_dataset_type": dataset.dataset_type,
            },
        )

        response = self.random_task_view(request)

        self.assertEqual(response.status_code, 404)
        self.assertTrue(response.data["details"]["raw_video_required"])

    def test_random_task_phi_dataset_allows_frames_when_raw_video_exists(self):
        dataset = AIDataSet.objects.create(
            name="frame-task-phi-dataset-raw",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type="phi_region_detector",
        )
        self.video.raw_file.name = "sensitive_videos/frame_task_raw.mp4"
        self.video.save(update_fields=["raw_file"])
        dataset.image_annotations.add(
            ImageClassificationAnnotation.objects.create(
                frame=self.frame_1,
                label=self.target_label,
                value=True,
                information_source=self.source,
                annotator="dataset",
            )
        )

        request = self.factory.get(
            "/api/media/annotations/frames/random-task/",
            {
                "video_id": self.video.pk,
                "ai_dataset_name": dataset.name,
                "ai_dataset_type": dataset.dataset_type,
                "exclude_annotated": "false",
            },
        )

        with patch(
            "endoreg_db.models.state.frame_annotation.random.randint",
            return_value=0,
        ):
            response = self.random_task_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["task"]["frame_id"], self.frame_1.pk)

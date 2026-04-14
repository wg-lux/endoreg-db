from django.test import TestCase
from rest_framework.test import APIRequestFactory

from endoreg_db.models import (
    Center,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    LabelSet,
    VideoFile,
)
from endoreg_db.views.video.ai import (
    FrameAnnotationRandomTaskView,
    FrameAnnotationSkipView,
)


class FrameAnnotationTaskEndpointsTest(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
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
        )
        self.frame_2 = Frame.objects.create(
            video=self.video,
            frame_number=11,
            relative_path="frame_0000011.jpg",
        )
        self.frame_3 = Frame.objects.create(
            video=self.video,
            frame_number=12,
            relative_path="frame_0000012.jpg",
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

    def test_random_task_returns_available_frame(self):
        request = self.factory.get(
            "/api/media/annotations/frames/random-task/",
            {
                "video_id": self.video.pk,
                "information_source_name": self.source.name,
            },
        )

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

    def test_random_task_filtered_mode_requires_filter_label(self):
        request = self.factory.get(
            "/api/media/annotations/frames/random-task/",
            {
                "video_id": self.video.pk,
                "task_mode": "filtered",
                "target_label": self.target_label.name,
            },
        )

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

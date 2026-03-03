from django.test import TestCase
from rest_framework.test import APIRequestFactory

from endoreg_db.models import (
    Center,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
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
        self.source = InformationSource.objects.create(name="manual_annotation")
        self.label = Label.objects.create(name="task-test-label")

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

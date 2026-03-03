from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory

from endoreg_db.models import (
    Center,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    VideoFile,
)
from endoreg_db.views.video.ai import LabelStudioWebhookReceiverView


class LabelStudioWebhookReceiverViewTest(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = LabelStudioWebhookReceiverView.as_view()

        self.center = Center.objects.create(name="ls-webhook-center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="ls-webhook-video-hash",
            original_file_name="ls_webhook.mp4",
            fps=25.0,
            frame_count=100,
        )
        self.frame = Frame.objects.create(
            video=self.video,
            frame_number=10,
            relative_path="frame_0000010.jpg",
        )

        self.label = Label.objects.create(name="Polyp Detected")
        self.source = InformationSource.objects.create(name="manual_annotation")
        self.user = get_user_model().objects.create_user(
            username="ls_annotator",
            password="test-password",
        )

    def _build_payload(self, choices: list[str]) -> dict:
        return {
            "action": "ANNOTATION_CREATED",
            "annotation": {
                "id": 1234,
                "task": 5678,
                "completed_by": self.user.pk,
                "was_cancelled": False,
                "ground_truth": False,
                "result": [
                    {
                        "id": "A1b2C3d4",
                        "from_name": "label",
                        "to_name": "frame_image",
                        "type": "choices",
                        "origin": "manual",
                        "value": {"choices": choices},
                    },
                    {
                        "id": "E5f6G7h8",
                        "from_name": "confidence",
                        "to_name": "frame_image",
                        "type": "rating",
                        "value": {"rating": 4},
                    },
                ],
            },
            "project": {"id": 1, "title": "EndoReg Video Annotation"},
            "task": {
                "id": 5678,
                "data": {
                    "video_url": "/media/videos/surgery_01.mp4",
                    "video_id": self.video.pk,
                    "frame_id": self.frame.pk,
                },
            },
        }

    @override_settings(
        LABEL_STUDIO_WEBHOOK_SECRET="topsecret",
        LABEL_STUDIO_INFORMATION_SOURCE_NAME="manual_annotation",
    )
    def test_webhook_maps_payload_and_upserts_annotations(self):
        payload = self._build_payload([self.label.name])
        request = self.factory.post(
            "/api/media/annotations/frames/label-studio-webhook/",
            payload,
            format="json",
            HTTP_AUTHORIZATION="Token topsecret",
        )

        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "success")
        self.assertEqual(response.data["upserted_count"], 1)
        self.assertEqual(response.data["action"], "ANNOTATION_CREATED")
        self.assertEqual(response.data["external_annotation_id"], "1234")

        created_annotation = ImageClassificationAnnotation.objects.get(
            frame=self.frame,
            label=self.label,
            information_source=self.source,
            annotator=self.user.username,
        )
        self.assertTrue(created_annotation.value)
        self.assertEqual(created_annotation.float_value, 4.0)
        self.assertEqual(created_annotation.external_annotation_id, "1234")

    @override_settings(
        LABEL_STUDIO_WEBHOOK_SECRET="topsecret",
        LABEL_STUDIO_INFORMATION_SOURCE_NAME="manual_annotation",
    )
    def test_webhook_rejects_invalid_secret(self):
        payload = self._build_payload([self.label.name])
        request = self.factory.post(
            "/api/media/annotations/frames/label-studio-webhook/",
            payload,
            format="json",
            HTTP_AUTHORIZATION="Token wrong-secret",
        )

        response = self.view(request)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            ImageClassificationAnnotation.objects.count(),
            0,
        )

    @override_settings(
        LABEL_STUDIO_WEBHOOK_SECRET="topsecret",
        LABEL_STUDIO_INFORMATION_SOURCE_NAME="manual_annotation",
    )
    def test_webhook_rejects_unknown_choice_label(self):
        payload = self._build_payload(["Unknown Label"])
        request = self.factory.post(
            "/api/media/annotations/frames/label-studio-webhook/",
            payload,
            format="json",
            HTTP_AUTHORIZATION="Token topsecret",
        )

        response = self.view(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["details"]["unknown_label_names"],
            ["Unknown Label"],
        )

    @override_settings(
        LABEL_STUDIO_WEBHOOK_SECRET="topsecret",
        LABEL_STUDIO_INFORMATION_SOURCE_NAME="manual_annotation",
    )
    def test_webhook_ignores_unsupported_actions(self):
        payload = {"action": "TASK_CREATED"}
        request = self.factory.post(
            "/api/media/annotations/frames/label-studio-webhook/",
            payload,
            format="json",
            HTTP_AUTHORIZATION="Token topsecret",
        )

        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "ignored")
        self.assertEqual(response.data["action"], "TASK_CREATED")

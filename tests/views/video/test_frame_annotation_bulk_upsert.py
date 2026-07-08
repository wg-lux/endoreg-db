from django.test import TestCase
from rest_framework.test import APIRequestFactory
import json
from endoreg_db.models import (
    Center,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    VideoFile,
)
from endoreg_db.views.video.ai import FrameAnnotationBulkUpsertView


class FrameAnnotationBulkUpsertViewTest(TestCase):
    factory: APIRequestFactory
    center: Center
    video: VideoFile
    other_video: VideoFile
    frame_1: Frame
    frame_2: Frame
    other_video_frame: Frame
    label: Label
    source: InformationSource

    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = FrameAnnotationBulkUpsertView.as_view()

        self.center = Center.objects.create(name="bulk-upsert-center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="bulk-upsert-video-hash",
            original_file_name="bulk_upsert.mp4",
            fps=25.0,
            frame_count=100,
        )
        self.other_video = VideoFile.objects.create(
            center=self.center,
            video_hash="bulk-upsert-video-hash-2",
            original_file_name="bulk_upsert_2.mp4",
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
        self.other_video_frame = Frame.objects.create(
            video=self.other_video,
            frame_number=11,
            relative_path="frame_0000011.jpg",
        )

        self.label = Label.objects.create(name="bulk-upsert-label")
        self.source = InformationSource.objects.create(name="annotation")

    def test_bulk_upsert_creates_then_updates_without_duplicates(self):
        create_payload = {
            "video_id": self.video.pk,
            "annotations": [
                {
                    "frame_id": self.frame_1.pk,
                    "label_id": self.label.pk,
                    "information_source_name": self.source.name,
                    "annotator": "bulk-user",
                    "value": True,
                },
                {
                    "frame_id": self.frame_2.pk,
                    "label_id": self.label.pk,
                    "information_source_name": self.source.name,
                    "annotator": "bulk-user",
                    "value": False,
                },
            ],
        }

        request = self.factory.post(
            "/api/media/annotations/frames/bulk-upsert/",
            create_payload,
            format="json",
        )
        response = self.view(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["upserted_count"], 2)
        self.assertEqual(
            ImageClassificationAnnotation.objects.filter(
                label=self.label,
                information_source=self.source,
                annotator="bulk-user",
            ).count(),
            2,
        )

        update_payload = {
            "video_id": self.video.pk,
            "annotations": [
                {
                    "frame_id": self.frame_2.pk,
                    "label_id": self.label.pk,
                    "information_source_name": self.source.name,
                    "annotator": "bulk-user",
                    "value": True,
                    "external_annotation_id": "ls-annotation-2",
                }
            ],
        }

        request = self.factory.post(
            "/api/media/annotations/frames/bulk-upsert/",
            update_payload,
            format="json",
        )
        response = self.view(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["upserted_count"], 1)

        self.assertEqual(
            ImageClassificationAnnotation.objects.filter(
                label=self.label,
                information_source=self.source,
                annotator="bulk-user",
            ).count(),
            2,
        )
        updated_annotation = ImageClassificationAnnotation.objects.get(
            frame=self.frame_2,
            label=self.label,
            information_source=self.source,
            annotator="bulk-user",
        )
        self.assertTrue(updated_annotation.value)
        self.assertEqual(updated_annotation.external_annotation_id, "ls-annotation-2")

    def test_bulk_upsert_rejects_unknown_information_source_name(self):
        payload = [
            {
                "frame_id": self.frame_1.pk,
                "label_id": self.label.pk,
                "information_source_name": "missing-source",
                "annotator": "bulk-user",
            }
        ]
        request = self.factory.post(
            "/api/media/annotations/frames/bulk-upsert/",
            payload,
            format="json",
        )

        response = self.view(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 400)
        self.assertIn("missing_information_source_names", data["details"])

    def test_bulk_upsert_rejects_frames_outside_requested_video(self):
        payload = {
            "video_id": self.video.pk,
            "annotations": [
                {
                    "frame_id": self.other_video_frame.pk,
                    "label_id": self.label.pk,
                    "information_source_name": self.source.name,
                    "annotator": "bulk-user",
                }
            ],
        }
        request = self.factory.post(
            "/api/media/annotations/frames/bulk-upsert/",
            payload,
            format="json",
        )

        response = self.view(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 400)
        self.assertIn("invalid_frame_ids", data["details"])

    def test_bulk_upsert_accepts_choice_name_and_inferrs_boolean_value(self):
        payload = {
            "video_id": self.video.pk,
            "annotations": [
                {
                    "frame_id": self.frame_1.pk,
                    "choice_name": f"{self.label.name}: present",
                    "information_source_name": self.source.name,
                    "annotator": "bulk-user",
                },
                {
                    "frame_id": self.frame_2.pk,
                    "choice_name": f"{self.label.name}: absent",
                    "information_source_name": self.source.name,
                    "annotator": "bulk-user",
                },
            ],
        }
        request = self.factory.post(
            "/api/media/annotations/frames/bulk-upsert/",
            payload,
            format="json",
        )
        response = self.view(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["upserted_count"], 2)

        ann_present = ImageClassificationAnnotation.objects.get(
            frame=self.frame_1,
            label=self.label,
            information_source=self.source,
            annotator="bulk-user",
        )
        ann_absent = ImageClassificationAnnotation.objects.get(
            frame=self.frame_2,
            label=self.label,
            information_source=self.source,
            annotator="bulk-user",
        )
        self.assertTrue(ann_present.value)
        self.assertFalse(ann_absent.value)

    def test_bulk_upsert_rejects_unknown_choice_name(self):
        payload = [
            {
                "frame_id": self.frame_1.pk,
                "choice_name": "definitely_unknown_label: present",
                "information_source_name": self.source.name,
                "annotator": "bulk-user",
            }
        ]
        request = self.factory.post(
            "/api/media/annotations/frames/bulk-upsert/",
            payload,
            format="json",
        )

        response = self.view(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 400)
        self.assertIn("choice_name", str(data))

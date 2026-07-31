from django.test import TestCase
from rest_framework.test import APIRequestFactory
import json
from typing import Protocol, cast
from endoreg_db.models import (
    Center,
    Frame,
    FrameBoxAnnotation,
    InformationSource,
    Label,
    VideoFile,
)
from endoreg_db.views.video.ai import FrameBoxAnnotationView


class _FrameBoxAnnotationLike(Protocol):
    frame: Frame
    label: Label
    external_annotation_id: str | None
    annotator: str | None
    x: float
    width: float

    def refresh_from_db(self) -> None: ...


class FrameBoxAnnotationViewTest(TestCase):
    factory: APIRequestFactory
    center: Center
    video: VideoFile
    other_video: VideoFile
    frame: Frame
    other_video_frame: Frame
    label: Label
    other_label: Label
    source: InformationSource

    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = FrameBoxAnnotationView.as_view()

        self.center = Center.objects.create(name="box-annotation-center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="box-annotation-video-hash",
            original_file_name="box_annotation.mp4",
            fps=25.0,
            frame_count=100,
        )
        self.other_video = VideoFile.objects.create(
            center=self.center,
            video_hash="box-annotation-video-hash-2",
            original_file_name="box_annotation_2.mp4",
            fps=25.0,
            frame_count=100,
        )
        self.frame = Frame.objects.create(
            video=self.video,
            frame_number=10,
            relative_path="frame_0000010.jpg",
        )
        self.other_video_frame = Frame.objects.create(
            video=self.other_video,
            frame_number=10,
            relative_path="frame_0000010.jpg",
        )
        self.label = Label.objects.create(name="patient_first_name")
        self.other_label = Label.objects.create(name="patient_last_name")
        self.source = InformationSource.objects.create(name="lx_anonymizer_evaluation")

    def test_box_annotations_create_list_and_update_by_external_id(self):
        payload = {
            "video_id": self.video.pk,
            "frame_id": self.frame.pk,
            "replace": True,
            "annotator": "box-user",
            "information_source_name": self.source.name,
            "annotations": [
                {
                    "label_id": self.label.pk,
                    "x": 10,
                    "y": 12,
                    "width": 100,
                    "height": 32,
                    "image_width": 800,
                    "image_height": 600,
                    "external_annotation_id": "box-1",
                }
            ],
        }

        request = self.factory.post(
            "/api/media/annotations/frames/boxes/",
            payload,
            format="json",
        )
        response = self.view(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["upserted_count"], 1)
        annotation = cast(_FrameBoxAnnotationLike, FrameBoxAnnotation.objects.get())
        self.assertEqual(annotation.frame, self.frame)
        self.assertEqual(annotation.label, self.label)
        self.assertEqual(annotation.annotator, "box-user")
        self.assertEqual(annotation.external_annotation_id, "box-1")

        update_payload = {
            "video_id": self.video.pk,
            "frame_id": self.frame.pk,
            "annotator": "box-user",
            "information_source_name": self.source.name,
            "annotations": [
                {
                    "label_id": self.label.pk,
                    "x": 20,
                    "y": 24,
                    "width": 120,
                    "height": 36,
                    "image_width": 800,
                    "image_height": 600,
                    "external_annotation_id": "box-1",
                }
            ],
        }
        request = self.factory.post(
            "/api/media/annotations/frames/boxes/",
            update_payload,
            format="json",
        )
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(FrameBoxAnnotation.objects.count(), 1)
        annotation.refresh_from_db()
        self.assertEqual(annotation.x, 20)
        self.assertEqual(annotation.width, 120)

        request = self.factory.get(
            "/api/media/annotations/frames/boxes/",
            {
                "frame_id": self.frame.pk,
                "information_source_name": self.source.name,
                "annotator": "box-user",
            },
        )
        response = self.view(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["annotations"][0]["label_name"], self.label.name)

    def test_box_replace_removes_stale_boxes_only_for_scope(self):
        FrameBoxAnnotation.objects.create(
            frame=self.frame,
            label=self.label,
            x=1,
            y=1,
            width=10,
            height=10,
            image_width=800,
            image_height=600,
            information_source=self.source,
            annotator="box-user",
            external_annotation_id="stale",
        )
        other_scope = FrameBoxAnnotation.objects.create(
            frame=self.frame,
            label=self.label,
            x=1,
            y=1,
            width=10,
            height=10,
            image_width=800,
            image_height=600,
            information_source=self.source,
            annotator="other-user",
            external_annotation_id="other",
        )

        payload = {
            "frame_id": self.frame.pk,
            "replace": True,
            "annotator": "box-user",
            "information_source_name": self.source.name,
            "annotations": [
                {
                    "label_id": self.other_label.pk,
                    "x": 30,
                    "y": 40,
                    "width": 50,
                    "height": 60,
                    "image_width": 800,
                    "image_height": 600,
                    "external_annotation_id": "fresh",
                }
            ],
        }
        request = self.factory.post(
            "/api/media/annotations/frames/boxes/",
            payload,
            format="json",
        )
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            FrameBoxAnnotation.objects.filter(external_annotation_id="stale").exists()
        )
        self.assertTrue(FrameBoxAnnotation.objects.filter(pk=other_scope.pk).exists())
        self.assertTrue(
            FrameBoxAnnotation.objects.filter(external_annotation_id="fresh").exists()
        )

    def test_box_replace_with_empty_annotations_clears_scope(self):
        FrameBoxAnnotation.objects.create(
            frame=self.frame,
            label=self.label,
            x=1,
            y=1,
            width=10,
            height=10,
            image_width=800,
            image_height=600,
            information_source=self.source,
            annotator="box-user",
        )

        request = self.factory.post(
            "/api/media/annotations/frames/boxes/",
            {
                "frame_id": self.frame.pk,
                "replace": True,
                "annotator": "box-user",
                "information_source_name": self.source.name,
                "annotations": [],
            },
            format="json",
        )
        response = self.view(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["deleted_count"], 1)
        self.assertEqual(FrameBoxAnnotation.objects.count(), 0)

    def test_box_annotation_rejects_out_of_bounds_box(self):
        payload = {
            "frame_id": self.frame.pk,
            "annotations": [
                {
                    "label_id": self.label.pk,
                    "information_source_name": self.source.name,
                    "x": 790,
                    "y": 12,
                    "width": 20,
                    "height": 32,
                    "image_width": 800,
                    "image_height": 600,
                }
            ],
        }
        request = self.factory.post(
            "/api/media/annotations/frames/boxes/",
            payload,
            format="json",
        )
        response = self.view(request)

        data = json.loads(response.content)

        self.assertEqual(response.status_code, 400)
        self.assertIn("width", str(data))

    def test_box_annotation_rejects_unknown_wrapper_and_item_fields(self):
        base_item = {
            "label_id": self.label.pk,
            "x": 10,
            "y": 12,
            "width": 20,
            "height": 32,
            "image_width": 800,
            "image_height": 600,
        }
        payloads = [
            {
                "frame_id": self.frame.pk,
                "annotations": [base_item],
                "unexpected_wrapper": True,
            },
            {
                "frame_id": self.frame.pk,
                "annotations": [{**base_item, "unexpected_item": True}],
            },
        ]

        for payload in payloads:
            with self.subTest(payload=payload):
                request = self.factory.post(
                    "/api/media/annotations/frames/boxes/",
                    payload,
                    format="json",
                )
                response = self.view(request)

                self.assertEqual(response.status_code, 400)
                self.assertEqual(FrameBoxAnnotation.objects.count(), 0)

    def test_box_annotation_rejects_conflicting_outer_and_item_frame_ids(self):
        request = self.factory.post(
            "/api/media/annotations/frames/boxes/",
            {
                "frame_id": self.frame.pk,
                "annotations": [
                    {
                        "frame_id": self.other_video_frame.pk,
                        "label_id": self.label.pk,
                        "information_source_name": self.source.name,
                        "x": 10,
                        "y": 12,
                        "width": 20,
                        "height": 32,
                        "image_width": 800,
                        "image_height": 600,
                    }
                ],
            },
            format="json",
        )

        response = self.view(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(FrameBoxAnnotation.objects.count(), 0)

    def test_box_annotation_rejects_frames_outside_requested_video(self):
        payload = {
            "video_id": self.video.pk,
            "frame_id": self.other_video_frame.pk,
            "annotations": [
                {
                    "label_id": self.label.pk,
                    "information_source_name": self.source.name,
                    "x": 10,
                    "y": 12,
                    "width": 20,
                    "height": 32,
                    "image_width": 800,
                    "image_height": 600,
                }
            ],
        }
        request = self.factory.post(
            "/api/media/annotations/frames/boxes/",
            payload,
            format="json",
        )
        response = self.view(request)
        data = json.loads(response.content)
        self.assertEqual(response.status_code, 400)
        self.assertIn("invalid_frame_ids", data["details"])

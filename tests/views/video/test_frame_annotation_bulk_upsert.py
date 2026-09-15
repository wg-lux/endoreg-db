from django.test import TestCase
from django.contrib.auth.models import User
from django.db import DatabaseError, IntegrityError, OperationalError
from rest_framework.test import APIRequestFactory, force_authenticate
from unittest.mock import patch
import json
from endoreg_db.models import (
    AIDataSet,
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

    def test_bulk_upsert_rejects_unknown_wrapper_and_item_fields(self):
        base_item = {
            "frame_id": self.frame_1.pk,
            "label_id": self.label.pk,
            "information_source_name": self.source.name,
        }
        payloads = [
            {"annotations": [base_item], "unexpected_wrapper": True},
            {"annotations": [{**base_item, "unexpected_item": True}]},
        ]

        for payload in payloads:
            with self.subTest(payload=payload):
                request = self.factory.post(
                    "/api/media/annotations/frames/bulk-upsert/",
                    payload,
                    format="json",
                )
                response = self.view(request)

                self.assertEqual(response.status_code, 400)
                self.assertEqual(ImageClassificationAnnotation.objects.count(), 0)

    def test_bulk_upsert_uses_authenticated_user_as_annotator(self):
        user = User.objects.create_user(username="trusted-reviewer")
        payload = [
            {
                "frame_id": self.frame_1.pk,
                "label_id": self.label.pk,
                "information_source_name": self.source.name,
            }
        ]
        request = self.factory.post(
            "/api/media/annotations/frames/bulk-upsert/",
            payload,
            format="json",
        )
        force_authenticate(request, user=user)

        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        annotation = ImageClassificationAnnotation.objects.get(frame=self.frame_1)
        self.assertEqual(annotation.annotator, "trusted-reviewer")

    def test_bulk_upsert_allows_cross_center_dataset_annotation(self):
        user = User.objects.create_user(username="foreign-reviewer")
        payload = [
            {
                "frame_id": self.frame_1.pk,
                "label_id": self.label.pk,
                "information_source_name": self.source.name,
            }
        ]
        request = self.factory.post(
            "/api/media/annotations/frames/bulk-upsert/",
            payload,
            format="json",
        )
        force_authenticate(request, user=user)

        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        annotation = ImageClassificationAnnotation.objects.get(frame=self.frame_1)
        self.assertEqual(annotation.annotator, "foreign-reviewer")

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

    def test_bulk_upsert_reports_retryable_database_failure_and_rolls_back(self):
        dataset = AIDataSet.objects.create(
            name="rollback-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        payload = {
            "video_id": self.video.pk,
            "ai_dataset_id": dataset.pk,
            "annotations": [
                {
                    "frame_id": self.frame_1.pk,
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

        with patch.object(
            AIDataSet,
            "add_frame_annotations",
            side_effect=OperationalError("storage unavailable"),
        ):
            response = self.view(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            data,
            {
                "status": "error",
                "error": "Frame annotation storage is temporarily unavailable.",
                "code": "frame_annotation_write_temporarily_unavailable",
                "retryable": True,
                "write_committed": False,
            },
        )
        self.assertEqual(ImageClassificationAnnotation.objects.count(), 0)
        self.assertEqual(dataset.image_annotations.count(), 0)

    def test_bulk_upsert_reports_non_retryable_integrity_conflict(self):
        payload = [
            {
                "frame_id": self.frame_1.pk,
                "label_id": self.label.pk,
                "information_source_name": self.source.name,
                "annotator": "bulk-user",
            }
        ]
        request = self.factory.post(
            "/api/media/annotations/frames/bulk-upsert/",
            payload,
            format="json",
        )

        with patch(
            "endoreg_db.views.video.ai.frame_annotations._persist_bulk_annotations",
            side_effect=IntegrityError("conflict"),
        ):
            response = self.view(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(data["code"], "frame_annotation_write_conflict")
        self.assertFalse(data["retryable"])
        self.assertFalse(data["write_committed"])

    def test_bulk_upsert_redacts_unclassified_database_failure(self):
        payload = [
            {
                "frame_id": self.frame_1.pk,
                "label_id": self.label.pk,
                "information_source_name": self.source.name,
                "annotator": "bulk-user",
            }
        ]
        request = self.factory.post(
            "/api/media/annotations/frames/bulk-upsert/",
            payload,
            format="json",
        )

        with patch(
            "endoreg_db.views.video.ai.frame_annotations._persist_bulk_annotations",
            side_effect=DatabaseError("sensitive database detail"),
        ):
            response = self.view(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(data["code"], "frame_annotation_write_failed")
        self.assertEqual(data["error"], "Frame annotations could not be saved.")
        self.assertNotIn("sensitive database detail", response.content.decode())
        self.assertFalse(data["retryable"])
        self.assertFalse(data["write_committed"])

    def test_bulk_upsert_does_not_mask_unexpected_programming_error(self):
        payload = [
            {
                "frame_id": self.frame_1.pk,
                "label_id": self.label.pk,
                "information_source_name": self.source.name,
                "annotator": "bulk-user",
            }
        ]
        request = self.factory.post(
            "/api/media/annotations/frames/bulk-upsert/",
            payload,
            format="json",
        )

        with (
            patch(
                "endoreg_db.views.video.ai.frame_annotations._persist_bulk_annotations",
                side_effect=RuntimeError("unexpected implementation failure"),
            ),
            self.assertRaisesRegex(RuntimeError, "unexpected implementation failure"),
        ):
            self.view(request)

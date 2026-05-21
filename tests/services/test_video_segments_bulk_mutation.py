from unittest.mock import MagicMock

from django.test import TestCase
from rest_framework import status

from endoreg_db.models import (
    AIDataSet,
    Center,
    InformationSource,
    Label,
    LabelVideoSegment,
    VideoFile,
)
from endoreg_db.services.video_segments_bulk_mutation import (
    BulkSegmentMutationServiceError,
    bulk_mutate_video_segments,
)


class VideoSegmentsBulkMutationServiceTest(TestCase):
    def setUp(self):
        self.center = Center.objects.create(name="Bulk Segment Service Center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="bulk-segments-service-video",
            original_file_name="bulk_segments_service.mp4",
            fps=25.0,
            frame_count=200,
        )
        self.label_a = Label.objects.create(name="bulk-service-outside")
        self.label_b = Label.objects.create(name="bulk-service-polyp")
        self.manual_source = InformationSource.objects.create(name="manual_annotation")
        self.update_segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label_a,
            start_frame_number=10,
            end_frame_number=20,
            source=self.manual_source,
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

    def test_service_shapes_dataset_attachment_response(self):
        dataset = AIDataSet.objects.create(
            name="bulk-segment-service-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_VIDEO,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_VIDEO_SEGMENT_CLASSIFICATION,
        )

        response_data = bulk_mutate_video_segments(
            video=self.video,
            payload={
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
        )

        created_id = response_data["created"][0]["segment"]["id"]
        self.assertEqual(response_data["created_count"], 1)
        self.assertEqual(response_data["updated_count"], 1)
        self.assertEqual(response_data["deleted_count"], 0)
        self.assertEqual(response_data["ai_dataset_id"], dataset.pk)
        self.assertEqual(
            response_data["attached_segment_ids"],
            sorted([created_id, self.update_segment.pk]),
        )
        self.assertEqual(response_data["dataset_video_annotation_count"], 2)
        self.assertTrue(dataset.video_annotations.filter(pk=created_id).exists())
        self.assertTrue(
            dataset.video_annotations.filter(pk=self.update_segment.pk).exists()
        )

    def test_service_uses_eager_annotation_sync_when_defer_disabled(self):
        sync_frame_annotations = MagicMock()
        delete_frame_annotations_for_segment = MagicMock(return_value=0)

        response_data = bulk_mutate_video_segments(
            video=self.video,
            payload={
                "defer_annotation_sync": False,
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
            sync_frame_annotations=sync_frame_annotations,
            delete_frame_annotations_for_segment=delete_frame_annotations_for_segment,
        )

        self.assertFalse(response_data["defer_annotation_sync"])
        self.assertEqual(sync_frame_annotations.call_count, 2)
        self.assertNotIn(
            "old_snapshot", sync_frame_annotations.call_args_list[0].kwargs
        )
        update_sync_kwargs = sync_frame_annotations.call_args_list[1].kwargs
        self.assertEqual(update_sync_kwargs["segment"].pk, self.update_segment.pk)
        self.assertEqual(update_sync_kwargs["old_snapshot"]["start_frame_number"], 10)
        self.assertEqual(update_sync_kwargs["old_snapshot"]["end_frame_number"], 20)
        delete_frame_annotations_for_segment.assert_not_called()

        state = self.video.get_or_create_state()
        self.assertTrue(state.segment_annotations_created)
        self.assertTrue(state.segment_annotations_validated)

    def test_service_rolls_back_created_segment_on_invalid_update(self):
        with self.assertRaises(BulkSegmentMutationServiceError) as error:
            bulk_mutate_video_segments(
                video=self.video,
                payload={
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
            )

        self.assertEqual(error.exception.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            error.exception.response_data["error"],
            "Invalid bulk segment payload",
        )
        self.assertFalse(
            LabelVideoSegment.objects.filter(
                video_file=self.video,
                start_frame_number=100,
                end_frame_number=125,
            ).exists()
        )
        self.update_segment.refresh_from_db()
        self.assertEqual(self.update_segment.start_frame_number, 10)
        self.assertEqual(self.update_segment.end_frame_number, 20)

    def test_service_rejects_empty_operation_set(self):
        with self.assertRaises(BulkSegmentMutationServiceError) as error:
            bulk_mutate_video_segments(
                video=self.video,
                payload={
                    "creates": [],
                    "updates": [],
                    "deletes": [],
                },
            )

        self.assertEqual(error.exception.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            error.exception.response_data,
            {"error": "At least one create, update, or delete is required."},
        )

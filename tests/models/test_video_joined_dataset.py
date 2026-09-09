from django.test import TestCase

from endoreg_db.models import AIDataSet, Center, VideoFile


class VideoJoinedDatasetTests(TestCase):
    def setUp(self) -> None:
        self.center = Center.objects.create(name="joined-video-dataset-center")

    def test_new_videos_share_the_system_joined_dataset(self) -> None:
        first_video = VideoFile.objects.create(
            center=self.center,
            video_hash="joined-video-dataset-first",
        )
        second_video = VideoFile.objects.create(
            center=self.center,
            video_hash="joined-video-dataset-second",
        )

        assert first_video.joined_dataset_id == second_video.joined_dataset_id
        assert first_video.joined_dataset.is_default_video_dataset is True
        assert first_video.joined_dataset.joined_videos.count() == 2

    def test_callers_can_assign_a_different_joined_dataset(self) -> None:
        assigned_dataset = AIDataSet.objects.create(
            name="assigned-video-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_VIDEO,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_VIDEO_SEGMENT_CLASSIFICATION,
        )

        video = VideoFile.objects.create(
            center=self.center,
            video_hash="joined-video-dataset-explicit",
            joined_dataset=assigned_dataset,
        )

        assert video.joined_dataset_id == assigned_dataset.pk
        assert list(assigned_dataset.get_related_videos_queryset()) == [video]

    def test_attach_video_assigns_the_explicit_dataset(self) -> None:
        video = VideoFile.objects.create(
            center=self.center,
            video_hash="joined-video-dataset-attach",
        )
        assigned_dataset = AIDataSet.objects.create(
            name="attached-video-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_VIDEO,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_VIDEO_SEGMENT_CLASSIFICATION,
        )

        assigned_dataset.attach_video(
            video,
            include_frame_annotations=False,
            include_video_annotations=False,
        )

        video.refresh_from_db()
        assert video.joined_dataset_id == assigned_dataset.pk

from django.test import TestCase

from endoreg_db.models import (
    AIDataSet,
    Center,
    Frame,
    ImageClassificationAnnotation,
    Label,
    LabelSet,
    LabelVideoSegment,
    VideoFile,
)
from endoreg_db.utils.ai.data_loader_for_model_input import (
    build_image_multilabel_dataset_from_db as build_input_dataset,
)
from endoreg_db.utils.ai.data_loader_for_model_training import (
    build_image_multilabel_dataset_from_db as build_training_dataset,
)
from endoreg_db.utils.ai.model_training.trainer_gastronet_multilabel import (
    groupwise_split_indices_by_video,
)


class ImageMultilabelDataLoaderVideoIdTests(TestCase):
    def setUp(self):
        center = Center.objects.create(name="video-id-backfill-center")
        self.video = VideoFile.objects.create(
            center=center,
            video_hash="video-id-backfill-video",
            original_file_name="video_id_backfill.mp4",
            fps=25.0,
            frame_count=2,
        )
        self.frames = [
            Frame.objects.create(
                video=self.video,
                frame_number=frame_number,
                relative_path=f"frame_{frame_number:07d}.jpg",
                is_extracted=True,
            )
            for frame_number in range(2)
        ]
        self.label = Label.objects.create(name="video-id-backfill-label")
        self.label_set = LabelSet.objects.create(
            name="video-id-backfill-label-set",
            version=1,
        )
        self.label_set.labels.add(self.label)
        self.dataset = AIDataSet.objects.create(
            name="video-id-backfill-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        annotations = [
            ImageClassificationAnnotation.objects.create(
                frame=frame,
                label=self.label,
                value=True,
                annotator="video-id-backfill",
            )
            for frame in self.frames
        ]
        self.dataset.image_annotations.add(*annotations)

    def test_training_loader_backfills_grouping_ids_with_video_id(self):
        payload = build_training_dataset(self.dataset, labelset=self.label_set)

        assert payload["frame_ids"] == [frame.pk for frame in self.frames]
        assert payload["video_ids"] == [self.video.pk, self.video.pk]
        assert "old_examination_ids" not in payload

    def test_model_input_loader_backfills_grouping_ids_with_video_id(self):
        payload = build_input_dataset(self.dataset, labelset=self.label_set)

        assert payload["frame_ids"] == [frame.pk for frame in self.frames]
        assert payload["video_ids"] == [self.video.pk, self.video.pk]
        assert "old_examination_ids" not in payload

    def test_training_loader_uses_dataset_video_annotations_as_samples(self):
        segment_dataset = AIDataSet.objects.create(
            name="video-id-segment-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=0,
            end_frame_number=2,
        )
        segment_dataset.video_annotations.add(segment)

        payload = build_training_dataset(segment_dataset, labelset=self.label_set)

        assert payload["frame_ids"] == [frame.pk for frame in self.frames]
        assert payload["video_ids"] == [self.video.pk, self.video.pk]
        assert payload["label_vectors"] == [[1], [1]]
        assert payload["label_masks"] == [[1], [1]]

    def test_model_input_loader_uses_dataset_video_annotations_as_samples(self):
        segment_dataset = AIDataSet.objects.create(
            name="video-id-input-segment-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=0,
            end_frame_number=2,
        )
        segment_dataset.video_annotations.add(segment)

        payload = build_input_dataset(segment_dataset, labelset=self.label_set)

        assert payload["frame_ids"] == [frame.pk for frame in self.frames]
        assert payload["video_ids"] == [self.video.pk, self.video.pk]
        assert payload["label_vectors"] == [[1], [1]]
        assert payload["label_masks"] == [[1], [1]]

    def test_training_loader_uses_all_annotation_sources_by_default(self):
        mixed_dataset = AIDataSet.objects.create(
            name="video-id-mixed-source-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        annotation = ImageClassificationAnnotation.objects.create(
            frame=self.frames[0],
            label=self.label,
            value=True,
            annotator="video-id-mixed-source",
        )
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=1,
            end_frame_number=2,
        )
        mixed_dataset.image_annotations.add(annotation)
        mixed_dataset.video_annotations.add(segment)

        payload = build_training_dataset(mixed_dataset, labelset=self.label_set)

        assert payload["frame_ids"] == [frame.pk for frame in self.frames]
        assert payload["label_vectors"] == [[1], [1]]

    def test_training_loader_can_use_frame_annotations_only(self):
        mixed_dataset = AIDataSet.objects.create(
            name="video-id-frame-source-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        annotation = ImageClassificationAnnotation.objects.create(
            frame=self.frames[0],
            label=self.label,
            value=True,
            annotator="video-id-frame-source",
        )
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=1,
            end_frame_number=2,
        )
        mixed_dataset.image_annotations.add(annotation)
        mixed_dataset.video_annotations.add(segment)

        payload = build_training_dataset(
            mixed_dataset,
            labelset=self.label_set,
            annotation_source_scope="frame_only",
        )

        assert payload["frame_ids"] == [self.frames[0].pk]
        assert payload["label_vectors"] == [[1]]

    def test_training_loader_can_use_segment_annotations_only(self):
        mixed_dataset = AIDataSet.objects.create(
            name="video-id-segment-source-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        annotation = ImageClassificationAnnotation.objects.create(
            frame=self.frames[0],
            label=self.label,
            value=True,
            annotator="video-id-segment-source",
        )
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            start_frame_number=1,
            end_frame_number=2,
        )
        mixed_dataset.image_annotations.add(annotation)
        mixed_dataset.video_annotations.add(segment)

        payload = build_training_dataset(
            mixed_dataset,
            labelset=self.label_set,
            annotation_source_scope="segment_only",
        )

        assert payload["frame_ids"] == [self.frames[1].pk]
        assert payload["label_vectors"] == [[1]]

    def test_groupwise_split_keeps_frames_from_same_video_together(self):
        train, val, test = groupwise_split_indices_by_video(
            frame_ids=[1, 2, 3],
            video_ids=[10, 10, 20],
            val_split=0.5,
            test_split=0.0,
            seed=1,
        )

        split_sets = [set(train), set(val), set(test)]
        assert any({0, 1}.issubset(split) for split in split_sets)

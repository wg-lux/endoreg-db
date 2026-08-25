from django.test import TestCase

from endoreg_db.models import (
    AIDataSet,
    Center,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    LabelSet,
    LabelVideoSegment,
    VideoFile,
)
from endoreg_db.services.aidataset_frame_buckets import (
    build_frame_bucket_distribution,
)


class AIDataSetFrameBucketDistributionTests(TestCase):
    def setUp(self):
        self.center = Center.objects.create(name="dataset-bucket-center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="dataset-bucket-video",
            original_file_name="dataset_bucket.mp4",
            fps=25.0,
            frame_count=4,
        )
        self.frames = [
            Frame.objects.create(
                video=self.video,
                frame_number=frame_number,
                relative_path=f"frame_{frame_number:07d}.jpg",
                is_extracted=True,
            )
            for frame_number in range(4)
        ]
        self.manual_source = InformationSource.objects.create(name="manual_annotation")
        self.prediction_source = InformationSource.objects.create(name="prediction")
        self.target_label = Label.objects.create(name="dataset-bucket-target")
        self.segment_label = Label.objects.create(name="dataset-bucket-segment")
        self.label_set = LabelSet.objects.create(
            name="dataset-bucket-label-set",
            version=1,
        )
        self.label_set.labels.add(self.target_label, self.segment_label)
        self.dataset = AIDataSet.objects.create(
            name="dataset-bucket-image",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )

    def test_frame_bucket_distribution_counts_target_and_label_buckets(self):
        positive_annotation = ImageClassificationAnnotation.objects.create(
            frame=self.frames[0],
            label=self.target_label,
            value=True,
            information_source=self.manual_source,
            annotator="dataset",
        )
        negative_annotation = ImageClassificationAnnotation.objects.create(
            frame=self.frames[1],
            label=self.target_label,
            value=False,
            information_source=self.manual_source,
            annotator="dataset",
        )
        unknown_annotation = ImageClassificationAnnotation.objects.create(
            frame=self.frames[2],
            label=self.segment_label,
            value=True,
            information_source=self.manual_source,
            annotator="dataset",
        )
        prediction_segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.segment_label,
            source=self.prediction_source,
            start_frame_number=self.frames[2].frame_number,
            end_frame_number=self.frames[3].frame_number + 1,
        )
        self.dataset.image_annotations.add(
            positive_annotation,
            negative_annotation,
            unknown_annotation,
        )
        self.dataset.video_annotations.add(prediction_segment)

        distribution = build_frame_bucket_distribution(
            self.dataset,
            label_set=self.label_set,
            target_label=self.target_label,
            prediction_segments_only=True,
        )
        payload = distribution.model_dump(mode="json")

        assert payload["target_buckets"] == [
            {"bucket": "positive", "frame_count": 1},
            {"bucket": "negative", "frame_count": 1},
            {"bucket": "unknown", "frame_count": 1},
        ]
        assert payload["annotation_frame_buckets"] == [
            {
                "label_id": self.segment_label.pk,
                "label_name": self.segment_label.name,
                "frame_count": 1,
            },
            {
                "label_id": self.target_label.pk,
                "label_name": self.target_label.name,
                "frame_count": 1,
            },
        ]
        assert payload["segment_frame_buckets"] == [
            {
                "label_id": self.segment_label.pk,
                "label_name": self.segment_label.name,
                "frame_count": 2,
            }
        ]
        assert payload["summary"]["annotation_frame_count"] == 2
        assert payload["summary"]["segment_frame_count"] == 2
        assert payload["summary"]["merged_frame_count"] == 3

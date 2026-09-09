from __future__ import annotations

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
from endoreg_db.services.frame_annotation_workflow import (
    build_annotation_frame_buckets,
    build_dataset_candidate_frame_ids,
    build_dataset_target_buckets,
    build_segment_frame_buckets,
    merge_frame_buckets,
)


class FrameAnnotationBucketBuilderUnitTests(TestCase):
    def setUp(self) -> None:
        self.center = Center.objects.create(name="queue-bucket-unit-center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="queue-bucket-unit-video",
            original_file_name="queue_bucket_unit.mp4",
            fps=25.0,
            frame_count=5,
        )
        self.frames = [
            Frame.objects.create(
                video=self.video,
                frame_number=frame_number,
                relative_path=f"frame_{frame_number:07d}.jpg",
                is_extracted=frame_number != 4,
            )
            for frame_number in range(5)
        ]
        self.manual_source = InformationSource.objects.create(name="manual_annotation")
        self.prediction_source = InformationSource.objects.create(name="prediction")
        self.target_label = Label.objects.create(name="Queue Target")
        self.segment_label = Label.objects.create(name="Queue Segment")
        self.excluded_label = Label.objects.create(name="Queue Excluded")
        self.label_set = LabelSet.objects.create(
            name="queue-bucket-unit-label-set",
            version=1,
        )
        self.label_set.labels.add(self.target_label, self.segment_label)
        self.dataset = AIDataSet.objects.create(
            name="queue-bucket-unit-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )

    def _annotation(
        self,
        *,
        frame: Frame,
        label: Label,
        value: bool,
        source: InformationSource | None = None,
        annotator: str = "queue-bucket-unit",
    ) -> ImageClassificationAnnotation:
        return ImageClassificationAnnotation.objects.create(
            frame=frame,
            label=label,
            value=value,
            information_source=source or self.manual_source,
            annotator=annotator,
        )

    def _segment(
        self,
        *,
        label: Label,
        source: InformationSource,
        start_frame_number: int,
        end_frame_number: int,
    ) -> LabelVideoSegment:
        return LabelVideoSegment.objects.create(
            video_file=self.video,
            label=label,
            source=source,
            start_frame_number=start_frame_number,
            end_frame_number=end_frame_number,
        )

    def test_target_builder_returns_empty_for_inapplicable_inputs(self) -> None:
        # Arrange
        video_dataset = AIDataSet.objects.create(
            name="queue-bucket-video-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_VIDEO,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_VIDEO_SEGMENT_CLASSIFICATION,
        )

        # Act
        without_dataset = build_dataset_target_buckets(
            dataset=None,
            target_label=self.target_label,
            require_extracted_frames=True,
        )
        without_target = build_dataset_target_buckets(
            dataset=self.dataset,
            target_label=None,
            require_extracted_frames=True,
        )
        wrong_dataset_type = build_dataset_target_buckets(
            dataset=video_dataset,
            target_label=self.target_label,
            require_extracted_frames=True,
        )
        empty_dataset = build_dataset_target_buckets(
            dataset=self.dataset,
            target_label=self.target_label,
            require_extracted_frames=True,
        )

        # Assert
        assert without_dataset == {}
        assert without_target == {}
        assert wrong_dataset_type == {}
        assert empty_dataset == {}

    def test_target_builder_classifies_frames_with_positive_precedence(self) -> None:
        # Arrange
        annotations = [
            self._annotation(
                frame=self.frames[0], label=self.target_label, value=False
            ),
            self._annotation(
                frame=self.frames[0],
                label=self.target_label,
                value=True,
                source=self.prediction_source,
            ),
            self._annotation(
                frame=self.frames[1], label=self.target_label, value=False
            ),
            self._annotation(
                frame=self.frames[2], label=self.segment_label, value=True
            ),
            self._annotation(frame=self.frames[4], label=self.target_label, value=True),
        ]
        self.dataset.image_annotations.add(*annotations)

        # Act
        extracted_buckets = build_dataset_target_buckets(
            dataset=self.dataset,
            target_label=self.target_label,
            require_extracted_frames=True,
        )
        all_buckets = build_dataset_target_buckets(
            dataset=self.dataset,
            target_label=self.target_label,
            require_extracted_frames=False,
        )

        # Assert
        assert extracted_buckets == {
            "positive": {self.frames[0].pk},
            "negative": {self.frames[1].pk},
            "unknown": {self.frames[2].pk},
        }
        assert all_buckets == {
            "positive": {self.frames[0].pk, self.frames[4].pk},
            "negative": {self.frames[1].pk},
            "unknown": {self.frames[2].pk},
        }

    def test_annotation_builder_filters_value_label_set_and_extraction(self) -> None:
        # Arrange
        annotations = [
            self._annotation(frame=self.frames[0], label=self.target_label, value=True),
            self._annotation(
                frame=self.frames[0],
                label=self.target_label,
                value=True,
                source=self.prediction_source,
            ),
            self._annotation(
                frame=self.frames[1], label=self.target_label, value=False
            ),
            self._annotation(
                frame=self.frames[2], label=self.segment_label, value=True
            ),
            self._annotation(
                frame=self.frames[3], label=self.excluded_label, value=True
            ),
            self._annotation(frame=self.frames[4], label=self.target_label, value=True),
        ]
        self.dataset.image_annotations.add(*annotations)

        # Act
        extracted_buckets = build_annotation_frame_buckets(
            dataset=self.dataset,
            label_set=self.label_set,
            require_extracted_frames=True,
        )
        all_buckets = build_annotation_frame_buckets(
            dataset=self.dataset,
            label_set=self.label_set,
            require_extracted_frames=False,
        )
        no_dataset_buckets = build_annotation_frame_buckets(
            dataset=None,
            label_set=self.label_set,
            require_extracted_frames=True,
        )

        # Assert
        assert extracted_buckets == {
            self.target_label.pk: {self.frames[0].pk},
            self.segment_label.pk: {self.frames[2].pk},
        }
        assert all_buckets == {
            self.target_label.pk: {self.frames[0].pk, self.frames[4].pk},
            self.segment_label.pk: {self.frames[2].pk},
        }
        assert no_dataset_buckets == {}

    def test_segment_builder_applies_prediction_label_and_frame_boundaries(
        self,
    ) -> None:
        # Arrange
        prediction_segment = self._segment(
            label=self.segment_label,
            source=self.prediction_source,
            start_frame_number=1,
            end_frame_number=5,
        )
        overlapping_prediction_segment = self._segment(
            label=self.segment_label,
            source=self.prediction_source,
            start_frame_number=2,
            end_frame_number=4,
        )
        manual_segment = self._segment(
            label=self.target_label,
            source=self.manual_source,
            start_frame_number=0,
            end_frame_number=2,
        )
        excluded_segment = self._segment(
            label=self.excluded_label,
            source=self.prediction_source,
            start_frame_number=0,
            end_frame_number=1,
        )
        self.dataset.video_annotations.add(
            prediction_segment,
            overlapping_prediction_segment,
            manual_segment,
            excluded_segment,
        )

        # Act
        prediction_extracted = build_segment_frame_buckets(
            dataset=self.dataset,
            label_set=self.label_set,
            only_prediction_segments=True,
            require_extracted_frames=True,
        )
        all_segments_and_frames = build_segment_frame_buckets(
            dataset=self.dataset,
            label_set=self.label_set,
            only_prediction_segments=False,
            require_extracted_frames=False,
        )
        no_dataset_buckets = build_segment_frame_buckets(
            dataset=None,
            label_set=self.label_set,
            only_prediction_segments=True,
            require_extracted_frames=True,
        )

        # Assert
        assert prediction_extracted == {
            self.segment_label.pk: {
                self.frames[1].pk,
                self.frames[2].pk,
                self.frames[3].pk,
            }
        }
        assert all_segments_and_frames == {
            self.target_label.pk: {self.frames[0].pk, self.frames[1].pk},
            self.segment_label.pk: {
                self.frames[1].pk,
                self.frames[2].pk,
                self.frames[3].pk,
                self.frames[4].pk,
            },
        }
        assert no_dataset_buckets == {}

    def test_candidate_builder_unions_annotations_and_prediction_segments(
        self,
    ) -> None:
        # Arrange
        negative_annotation = self._annotation(
            frame=self.frames[0], label=self.target_label, value=False
        )
        excluded_annotation = self._annotation(
            frame=self.frames[1], label=self.excluded_label, value=True
        )
        unextracted_annotation = self._annotation(
            frame=self.frames[4], label=self.target_label, value=True
        )
        prediction_segment = self._segment(
            label=self.segment_label,
            source=self.prediction_source,
            start_frame_number=2,
            end_frame_number=4,
        )
        self.dataset.image_annotations.add(
            negative_annotation,
            excluded_annotation,
            unextracted_annotation,
        )
        self.dataset.video_annotations.add(prediction_segment)

        # Act
        extracted_candidates = build_dataset_candidate_frame_ids(
            dataset=self.dataset,
            label_set=self.label_set,
            only_prediction_segments=True,
            require_extracted_frames=True,
        )
        all_candidates = build_dataset_candidate_frame_ids(
            dataset=self.dataset,
            label_set=self.label_set,
            only_prediction_segments=True,
            require_extracted_frames=False,
        )

        # Assert
        assert extracted_candidates == {
            self.frames[0].pk,
            self.frames[2].pk,
            self.frames[3].pk,
        }
        assert all_candidates == {
            self.frames[0].pk,
            self.frames[2].pk,
            self.frames[3].pk,
            self.frames[4].pk,
        }

    def test_candidate_builder_distinguishes_no_dataset_from_empty_dataset(
        self,
    ) -> None:
        # Arrange / Act
        without_dataset = build_dataset_candidate_frame_ids(
            dataset=None,
            label_set=None,
            only_prediction_segments=True,
            require_extracted_frames=True,
        )
        empty_dataset = build_dataset_candidate_frame_ids(
            dataset=self.dataset,
            label_set=None,
            only_prediction_segments=True,
            require_extracted_frames=True,
        )

        # Assert
        assert without_dataset is None
        assert empty_dataset == set()

    def test_merge_builder_unions_matching_labels_and_removes_empty_buckets(
        self,
    ) -> None:
        # Arrange
        annotation_buckets: dict[int, set[int]] = {
            self.target_label.pk: {self.frames[0].pk},
            self.segment_label.pk: set(),
        }
        segment_buckets: dict[int, set[int]] = {
            self.target_label.pk: {self.frames[0].pk, self.frames[1].pk},
            self.segment_label.pk: {self.frames[2].pk},
        }

        # Act
        merged = merge_frame_buckets(annotation_buckets, segment_buckets)

        # Assert
        assert merged == {
            self.target_label.pk: {self.frames[0].pk, self.frames[1].pk},
            self.segment_label.pk: {self.frames[2].pk},
        }

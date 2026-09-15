from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.test import SimpleTestCase, TestCase

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
from endoreg_db.services import aidataset_frame_buckets as bucket_service


class AIDataSetFrameBucketPureUnitTests(SimpleTestCase):
    def test_model_value_helpers_normalize_supported_values(self) -> None:
        # Arrange
        timestamp = datetime(2026, 8, 28, tzinfo=timezone.utc)
        instance = SimpleNamespace(
            text="value",
            empty="",
            truthy=1,
            bool_number=True,
            integer=3,
            float_number=4.9,
            string_number="5",
            optional_number=None,
            timestamp=timestamp,
        )

        # Act
        values = {
            "text": bucket_service._model_text(instance, "text"),
            "empty_text": bucket_service._model_text(instance, "empty"),
            "optional_text": bucket_service._model_optional_text(instance, "empty"),
            "bool": bucket_service._model_bool(instance, "truthy"),
            "bool_number": bucket_service._model_int(instance, "bool_number"),
            "integer": bucket_service._model_int(instance, "integer"),
            "float_number": bucket_service._model_int(instance, "float_number"),
            "string_number": bucket_service._model_int(instance, "string_number"),
            "optional_number": bucket_service._model_optional_int(
                instance, "optional_number"
            ),
            "timestamp": bucket_service._model_datetime(instance, "timestamp"),
        }

        # Assert
        assert values == {
            "text": "value",
            "empty_text": "",
            "optional_text": None,
            "bool": True,
            "bool_number": 1,
            "integer": 3,
            "float_number": 4,
            "string_number": 5,
            "optional_number": None,
            "timestamp": timestamp,
        }

    def test_model_value_helpers_reject_invalid_numeric_and_datetime_values(
        self,
    ) -> None:
        # Arrange
        instance = SimpleNamespace(number=object(), timestamp="not-a-datetime")

        # Act / Assert
        with pytest.raises(TypeError, match="number must be numeric"):
            bucket_service._model_int(instance, "number")
        with pytest.raises(TypeError, match="timestamp must be a datetime"):
            bucket_service._model_datetime(instance, "timestamp")

    def test_label_filtering_handles_missing_and_restricted_label_sets(self) -> None:
        # Arrange
        label_set = MagicMock()
        label_set.labels.filter.return_value.exists.return_value = False

        # Act
        missing_label_allowed = bucket_service._label_allowed_by_set(None, None)
        unrestricted_label_allowed = bucket_service._label_allowed_by_set(7, None)
        restricted_label_allowed = bucket_service._label_allowed_by_set(7, label_set)

        # Assert
        assert missing_label_allowed is False
        assert unrestricted_label_allowed is True
        assert restricted_label_allowed is False
        label_set.labels.filter.assert_called_once_with(pk=7)

    def test_bucket_collection_helpers_sort_merge_deduplicate_and_drop_empty(
        self,
    ) -> None:
        # Arrange
        first_buckets: dict[int, set[int]] = {2: {20}, 1: {10, 11}, 3: set()}
        second_buckets: dict[int, set[int]] = {1: {11, 12}, 4: set()}

        # Act
        merged = bucket_service._merge_label_frame_buckets(
            first_buckets,
            second_buckets,
        )
        frame_ids = bucket_service._union_frame_bucket_values(merged)
        serialized = bucket_service._serialize_label_frame_buckets(
            merged,
            label_names_by_id={1: "Alpha"},
        )

        # Assert
        assert merged == {1: {10, 11, 12}, 2: {20}}
        assert frame_ids == {10, 11, 12, 20}
        assert [item.model_dump() for item in serialized] == [
            {"label_id": 1, "label_name": "Alpha", "frame_count": 3},
            {"label_id": 2, "label_name": "Label 2", "frame_count": 1},
        ]

    def test_invalid_segment_range_does_not_create_a_bucket(self) -> None:
        # Arrange
        invalid_segment = SimpleNamespace(
            label_id=3,
            video_file_id=9,
            start_frame_number=5,
            end_frame_number=5,
            source=SimpleNamespace(name="prediction"),
            prediction_meta_id=None,
        )
        segments = MagicMock()
        segments.select_related.return_value.filter.return_value.order_by.return_value.iterator.return_value = [
            invalid_segment
        ]
        dataset = SimpleNamespace(video_annotations=segments)

        # Act
        result = bucket_service._build_segment_frame_buckets(
            dataset,  # type: ignore[arg-type]
            label_set=None,
            prediction_segments_only=True,
        )

        # Assert
        assert result == {}


class AIDataSetFrameBucketDatabaseUnitTests(TestCase):
    def setUp(self) -> None:
        self.center = Center.objects.create(name="bucket-unit-center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="bucket-unit-video",
            original_file_name="bucket_unit.mp4",
            fps=25.0,
            frame_count=6,
        )
        self.frames = [
            Frame.objects.create(
                video=self.video,
                frame_number=frame_number,
                relative_path=f"frame_{frame_number:07d}.jpg",
                is_extracted=frame_number != 5,
            )
            for frame_number in range(6)
        ]
        self.manual_source = InformationSource.objects.create(name="manual_annotation")
        self.prediction_source = InformationSource.objects.create(name="prediction")
        self.target_label = Label.objects.create(name="Target")
        self.segment_label = Label.objects.create(name="Segment")
        self.excluded_label = Label.objects.create(name="Excluded")
        self.label_set = LabelSet.objects.create(
            name="bucket-unit-label-set", version=1
        )
        self.label_set.labels.add(self.target_label, self.segment_label)
        self.dataset = AIDataSet.objects.create(
            name="bucket-unit-dataset",
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
        annotator: str = "bucket-unit",
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
        start: int,
        end: int,
    ) -> LabelVideoSegment:
        return LabelVideoSegment.objects.create(
            video_file=self.video,
            label=label,
            source=source,
            start_frame_number=start,
            end_frame_number=end,
        )

    def test_empty_dataset_creates_stable_empty_buckets_and_summary(self) -> None:
        # Arrange
        expected_target_buckets = [
            {"bucket": "positive", "frame_count": 0},
            {"bucket": "negative", "frame_count": 0},
            {"bucket": "unknown", "frame_count": 0},
        ]

        # Act
        distribution = bucket_service.build_frame_bucket_distribution(
            self.dataset,
            label_set=self.label_set,
            target_label=self.target_label,
        ).model_dump(mode="json")

        # Assert
        assert distribution["target_buckets"] == expected_target_buckets
        assert distribution["label_distribution"] == []
        assert distribution["annotation_frame_buckets"] == []
        assert distribution["segment_frame_buckets"] == []
        assert distribution["merged_frame_buckets"] == []
        assert distribution["summary"] == {
            "image_annotation_count": 0,
            "video_annotation_count": 0,
            "annotation_frame_count": 0,
            "segment_frame_count": 0,
            "merged_frame_count": 0,
            "video_count": 0,
            "label_count": 0,
        }

    def test_non_image_dataset_does_not_create_target_buckets(self) -> None:
        # Arrange
        dataset = AIDataSet.objects.create(
            name="bucket-unit-video-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_VIDEO,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_VIDEO_SEGMENT_CLASSIFICATION,
        )

        # Act
        distribution = bucket_service.build_frame_bucket_distribution(
            dataset,
            target_label=self.target_label,
        ).model_dump(mode="json")

        # Assert
        assert distribution["target_buckets"] == [
            {"bucket": "positive", "frame_count": 0},
            {"bucket": "negative", "frame_count": 0},
            {"bucket": "unknown", "frame_count": 0},
        ]
        assert distribution["label_group_id"] is None
        assert distribution["target_label_id"] == self.target_label.pk

    def test_target_buckets_apply_positive_precedence_and_extracted_filter(
        self,
    ) -> None:
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
            self._annotation(frame=self.frames[5], label=self.target_label, value=True),
        ]
        self.dataset.image_annotations.add(*annotations)

        # Act
        distribution = bucket_service.build_frame_bucket_distribution(
            self.dataset,
            target_label=self.target_label,
        ).model_dump(mode="json")

        # Assert
        assert distribution["target_buckets"] == [
            {"bucket": "positive", "frame_count": 1},
            {"bucket": "negative", "frame_count": 1},
            {"bucket": "unknown", "frame_count": 1},
        ]
        assert distribution["summary"]["image_annotation_count"] == 5
        assert distribution["summary"]["annotation_frame_count"] == 2

    def test_label_set_filters_annotation_segment_and_distribution_buckets(
        self,
    ) -> None:
        # Arrange
        allowed_annotation = self._annotation(
            frame=self.frames[0], label=self.target_label, value=True
        )
        negative_annotation = self._annotation(
            frame=self.frames[1], label=self.target_label, value=False
        )
        excluded_annotation = self._annotation(
            frame=self.frames[2], label=self.excluded_label, value=True
        )
        allowed_segment = self._segment(
            label=self.segment_label,
            source=self.prediction_source,
            start=1,
            end=4,
        )
        excluded_segment = self._segment(
            label=self.excluded_label,
            source=self.prediction_source,
            start=0,
            end=1,
        )
        self.dataset.image_annotations.add(
            allowed_annotation,
            negative_annotation,
            excluded_annotation,
        )
        self.dataset.video_annotations.add(allowed_segment, excluded_segment)

        # Act
        distribution = bucket_service.build_frame_bucket_distribution(
            self.dataset,
            label_set=self.label_set,
            target_label=self.target_label,
        ).model_dump(mode="json")

        # Assert
        assert distribution["label_distribution"] == [
            {
                "label_id": self.target_label.pk,
                "label_name": self.target_label.name,
                "frame_positive": 1,
                "frame_negative": 1,
                "segment_count": 0,
                "total": 2,
            },
            {
                "label_id": self.segment_label.pk,
                "label_name": self.segment_label.name,
                "frame_positive": 0,
                "frame_negative": 0,
                "segment_count": 1,
                "total": 1,
            },
        ]
        assert distribution["annotation_frame_buckets"] == [
            {
                "label_id": self.target_label.pk,
                "label_name": self.target_label.name,
                "frame_count": 1,
            }
        ]
        assert distribution["segment_frame_buckets"] == [
            {
                "label_id": self.segment_label.pk,
                "label_name": self.segment_label.name,
                "frame_count": 3,
            }
        ]
        assert distribution["merged_frame_buckets"] == [
            {
                "label_id": self.segment_label.pk,
                "label_name": self.segment_label.name,
                "frame_count": 3,
            },
            {
                "label_id": self.target_label.pk,
                "label_name": self.target_label.name,
                "frame_count": 1,
            },
        ]
        assert distribution["summary"] == {
            "image_annotation_count": 3,
            "video_annotation_count": 2,
            "annotation_frame_count": 1,
            "segment_frame_count": 3,
            "merged_frame_count": 4,
            "video_count": 1,
            "label_count": 2,
        }

    def test_segment_buckets_filter_manual_segments_and_use_exclusive_end(
        self,
    ) -> None:
        # Arrange
        prediction_segment = self._segment(
            label=self.segment_label,
            source=self.prediction_source,
            start=3,
            end=6,
        )
        manual_segment = self._segment(
            label=self.target_label,
            source=self.manual_source,
            start=0,
            end=2,
        )
        self.dataset.video_annotations.add(prediction_segment, manual_segment)

        # Act
        prediction_only = bucket_service.build_frame_bucket_distribution(
            self.dataset,
            label_set=self.label_set,
            prediction_segments_only=True,
        ).model_dump(mode="json")
        all_segments = bucket_service.build_frame_bucket_distribution(
            self.dataset,
            label_set=self.label_set,
            prediction_segments_only=False,
        ).model_dump(mode="json")

        # Assert
        assert prediction_only["segment_frame_buckets"] == [
            {
                "label_id": self.segment_label.pk,
                "label_name": self.segment_label.name,
                "frame_count": 2,
            }
        ]
        assert all_segments["segment_frame_buckets"] == [
            {
                "label_id": self.segment_label.pk,
                "label_name": self.segment_label.name,
                "frame_count": 2,
            },
            {
                "label_id": self.target_label.pk,
                "label_name": self.target_label.name,
                "frame_count": 2,
            },
        ]
        assert all_segments["prediction_segments_only"] is False

    def test_merged_bucket_deduplicates_frame_shared_by_annotation_and_segment(
        self,
    ) -> None:
        # Arrange
        annotation = self._annotation(
            frame=self.frames[1], label=self.segment_label, value=True
        )
        segment = self._segment(
            label=self.segment_label,
            source=self.prediction_source,
            start=1,
            end=3,
        )
        self.dataset.image_annotations.add(annotation)
        self.dataset.video_annotations.add(segment)

        # Act
        distribution = bucket_service.build_frame_bucket_distribution(
            self.dataset,
            label_set=self.label_set,
        ).model_dump(mode="json")

        # Assert
        assert distribution["annotation_frame_buckets"][0]["frame_count"] == 1
        assert distribution["segment_frame_buckets"][0]["frame_count"] == 2
        assert distribution["merged_frame_buckets"][0]["frame_count"] == 2
        assert distribution["summary"]["merged_frame_count"] == 2
        assert distribution["label_group_id"] == self.label_set.pk
        assert distribution["label_group_name"] == self.label_set.name
        assert distribution["target_label_id"] is None
        assert distribution["target_label_name"] is None

"""Database-backed AAA checks derived from the local dataset audit.

These exercise membership and label contracts, not on-disk media acceptance.
"""

from django.test import TestCase
from django.utils import timezone

from endoreg_db.models import (
    AIDataSet,
    Center,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    LabelSet,
    VideoFile,
    VideoState,
)
from endoreg_db.services.datasets.training_manifests import (
    build_frame_multilabel_training_manifest,
)


class DatasetReadinessAAATests(TestCase):
    def setUp(self) -> None:
        self.dataset = AIDataSet.objects.create(name="readiness-contract")
        self.source = InformationSource.objects.create(name="manual_annotation")
        self.positive = Label.objects.create(name="readiness-positive")
        self.unknown = Label.objects.create(name="readiness-unknown")
        self.label_set = LabelSet.objects.create(name="readiness-label-set", version=1)
        self.label_set.labels.add(self.positive, self.unknown)
        self.state = VideoState.objects.create(
            anonymized=True,
            anonymization_validated=True,
            segment_annotations_validated=True,
            outside_segments_removed=True,
            ready_for_export=True,
            ready_for_export_at=timezone.now(),
            ready_for_export_by="test-suite",
            processed_file_sha256="a" * 64,
        )
        self.video = VideoFile.objects.create(
            center=Center.objects.create(name="readiness-center"),
            raw_video_hash="readiness-video",
            processed_file="processed_videos_final/readiness.mp4",
            fps=50,
            frame_count=1,
            state=self.state,
        )
        self.frame = Frame.objects.create(
            video=self.video,
            frame_number=0,
            timestamp=0,
            presentation_timestamp=0,
            relative_path="readiness/frame_0000000.jpg",
        )
        self.annotation = ImageClassificationAnnotation.objects.create(
            frame=self.frame,
            label=self.positive,
            value=True,
            information_source=self.source,
            annotator="readiness-reviewer",
        )

    def test_video_membership_requires_explicit_annotation_attachment(self) -> None:
        # Arrange: the default dataset contains the video, but no annotations.
        joined = self.video.joined_dataset
        assert joined.get_related_videos_queryset().filter(pk=self.video.pk).exists()

        # Act / Assert: membership alone cannot create a training manifest.
        with self.assertRaisesRegex(ValueError, "no extracted or validated"):
            build_frame_multilabel_training_manifest(
                joined, label_set=self.label_set, check_frame_format=False
            )

        # Act: use the existing attachment boundary; repeat to check idempotency.
        before = ImageClassificationAnnotation.objects.count()
        for _ in range(2):
            self.dataset.attach_video(self.video, include_video_annotations=False)
        manifest = build_frame_multilabel_training_manifest(
            self.dataset, label_set=self.label_set, check_frame_format=False
        )

        # Assert: attachment reuses the annotation and yields one sample.
        self.video.refresh_from_db()
        assert self.video.joined_dataset_id == self.dataset.pk
        assert self.dataset.image_annotations.count() == 1
        assert ImageClassificationAnnotation.objects.count() == before
        assert [sample.frame_id for sample in manifest.samples] == [self.frame.pk]

    def test_positive_only_annotation_preserves_unknown_labels_by_default(self) -> None:
        # Arrange
        self.dataset.image_annotations.add(self.annotation)

        # Act
        manifest = build_frame_multilabel_training_manifest(
            self.dataset, label_set=self.label_set, check_frame_format=False
        )

        # Assert: absent review is unknown, never an implicit negative.
        assert [label.name for label in manifest.labels] == [
            self.positive.name,
            self.unknown.name,
        ]
        assert manifest.samples[0].labels == [1.0, 0.0]
        assert manifest.samples[0].label_mask == [1, 0]
        assert manifest.provenance["treat_unlabeled_as_negative"] is False

    def test_reviewer_confirmation_turns_unknown_into_explicit_negative(self) -> None:
        # Arrange
        negative = ImageClassificationAnnotation.objects.create(
            frame=self.frame,
            label=self.unknown,
            value=False,
            information_source=self.source,
            annotator="readiness-reviewer",
        )
        self.dataset.image_annotations.add(self.annotation, negative)

        # Act
        manifest = build_frame_multilabel_training_manifest(
            self.dataset, label_set=self.label_set, check_frame_format=False
        )

        # Assert
        assert manifest.samples[0].labels == [1.0, 0.0]
        assert manifest.samples[0].label_mask == [1, 1]

    def test_attachment_cannot_bypass_pending_segment_validation(self) -> None:
        # Arrange: anonymization acceptance remains distinct from segment review.
        VideoState.objects.filter(pk=self.state.pk).update(
            segment_annotations_validated=False, ready_for_export=False
        )

        # Act
        self.dataset.attach_video(self.video, include_video_annotations=False)

        # Assert
        assert self.dataset.image_annotations.count() == 1
        with self.assertRaisesRegex(ValueError, "no extracted or validated"):
            build_frame_multilabel_training_manifest(
                self.dataset, label_set=self.label_set, check_frame_format=False
            )
        self.state.refresh_from_db()
        assert self.state.anonymization_validated is True
        assert self.state.segment_annotations_validated is False

    def test_manifest_video_group_does_not_prove_patient_split_readiness(self) -> None:
        # Arrange: reproduce the accepted videos lacking patient links.
        assert self.video.patient_id is None
        self.dataset.image_annotations.add(self.annotation)

        # Act
        manifest = build_frame_multilabel_training_manifest(
            self.dataset, label_set=self.label_set, check_frame_format=False
        )

        # Assert: characterize the current contract, not a patient-safe split.
        assert manifest.samples[0].group_id == str(self.video.uuid)
        assert manifest.samples[0].video_uuid == str(self.video.uuid)
        assert self.video.patient_id is None

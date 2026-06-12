from django.test import TestCase

from endoreg_db.models import (
    AiModel,
    Center,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    LabelSet,
    LabelVideoSegment,
    ModelMeta,
    VideoFile,
    VideoPredictionMeta,
)
from endoreg_db.models.state.frame_annotation import (
    SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX,
    segment_derived_external_annotation_id,
)
from endoreg_db.services.frame_segment_reconciliation import (
    FrameSegmentReconciliationSpec,
    reconcile_frame_segment_annotations,
)


class FrameSegmentReconciliationServiceTest(TestCase):
    def setUp(self):
        self.center = Center.objects.create(name="frame-segment-reconcile-center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="frame-segment-reconcile-video",
            original_file_name="frame_segment_reconcile.mp4",
            fps=25.0,
            frame_count=5,
        )
        self.frames = [
            Frame.objects.create(
                video=self.video,
                frame_number=frame_number,
                relative_path=f"frame_{frame_number:07d}.jpg",
                is_extracted=True,
            )
            for frame_number in range(5)
        ]
        self.manual_source = InformationSource.objects.create(name="manual_annotation")
        self.prediction_source = InformationSource.objects.create(name="prediction")
        self.prediction_annotation_source = InformationSource.objects.create(
            name="prediction_annotation"
        )
        self.label = Label.objects.create(name="reconcile-polyp")
        self.other_label = Label.objects.create(name="reconcile-other")
        self.label_set = LabelSet.objects.create(
            name="reconcile-label-set",
            version=1,
        )
        self.label_set.labels.add(self.label, self.other_label)
        self.ai_model = AiModel.objects.create(name="reconcile-model")
        self.model_meta = ModelMeta.objects.create(
            name="reconcile-model-meta",
            version="1",
            model=self.ai_model,
            labelset=self.label_set,
        )
        self.prediction_meta = VideoPredictionMeta.objects.create(
            video_file=self.video,
            model_meta=self.model_meta,
        )

    def _manual_segment(self, *, start: int = 0, end: int = 2):
        return LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            source=self.manual_source,
            start_frame_number=start,
            end_frame_number=end,
        )

    def _prediction_segment(self, *, start: int = 0, end: int = 2):
        return LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label,
            source=self.prediction_source,
            prediction_meta=self.prediction_meta,
            start_frame_number=start,
            end_frame_number=end,
        )

    def test_missing_manual_annotations_are_reported_and_created_with_marker(self):
        self._manual_segment(start=0, end=2)

        dry_report = reconcile_frame_segment_annotations(
            FrameSegmentReconciliationSpec(video_ids=(self.video.pk,))
        )
        self.assertEqual(dry_report.summary.missing_annotations, 2)
        self.assertEqual(dry_report.summary.created_annotations, 0)
        self.assertEqual(ImageClassificationAnnotation.objects.count(), 0)

        apply_report = reconcile_frame_segment_annotations(
            FrameSegmentReconciliationSpec(
                video_ids=(self.video.pk,),
                apply=True,
            )
        )

        self.assertEqual(apply_report.summary.created_annotations, 2)
        annotations = ImageClassificationAnnotation.objects.order_by(
            "frame__frame_number"
        )
        self.assertEqual(annotations.count(), 2)
        
        self.assertTrue(
            all(
                (
                    annotation.external_annotation_id is not None
                    and annotation.external_annotation_id.startswith(
                        f"{SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX}:"
                    )
                )
                for annotation in annotations
            )
        )

    def test_missing_prediction_annotations_preserve_model_provenance(self):
        self._prediction_segment(start=0, end=2)

        report = reconcile_frame_segment_annotations(
            FrameSegmentReconciliationSpec(
                video_ids=(self.video.pk,),
                track="prediction",
                apply=True,
            )
        )

        self.assertEqual(report.summary.created_annotations, 2)
        annotations = ImageClassificationAnnotation.objects.order_by(
            "frame__frame_number"
        )
        self.assertEqual(annotations.count(), 2)
        self.assertTrue(
            all(
                getattr(annotation, "model_meta_id", None) == self.model_meta.pk
                and getattr(annotation, "information_source_id", None)
                == self.prediction_annotation_source.pk
                for annotation in annotations
            )
        )

    def test_legacy_unmarked_annotation_prevents_duplicate_creation(self):
        self._manual_segment(start=0, end=2)
        ImageClassificationAnnotation.objects.create(
            frame=self.frames[0],
            label=self.label,
            value=True,
            information_source=self.manual_source,
        )

        report = reconcile_frame_segment_annotations(
            FrameSegmentReconciliationSpec(
                video_ids=(self.video.pk,),
                apply=True,
            )
        )

        self.assertEqual(report.summary.legacy_matched_annotations, 1)
        self.assertEqual(report.summary.created_annotations, 1)
        self.assertEqual(
            ImageClassificationAnnotation.objects.filter(
                frame=self.frames[0],
                label=self.label,
                information_source=self.manual_source,
            ).count(),
            1,
        )
        self.assertEqual(ImageClassificationAnnotation.objects.count(), 2)

    def test_direct_manual_frame_annotation_takes_preference_over_segment_annotation(
        self,
    ):
        self._manual_segment(start=0, end=2)
        frame_source = InformationSource.objects.create(
            name="frame_annotation_frontend"
        )
        ImageClassificationAnnotation.objects.create(
            frame=self.frames[0],
            label=self.label,
            value=False,
            information_source=frame_source,
        )

        report = reconcile_frame_segment_annotations(
            FrameSegmentReconciliationSpec(
                video_ids=(self.video.pk,),
                apply=True,
            )
        )

        self.assertEqual(report.summary.legacy_matched_annotations, 1)
        self.assertEqual(report.summary.created_annotations, 1)
        self.assertFalse(
            ImageClassificationAnnotation.objects.filter(
                frame=self.frames[0],
                information_source=self.manual_source,
                external_annotation_id__startswith=(
                    f"{SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX}:"
                ),
            ).exists()
        )

    def test_stale_marker_backed_annotation_is_deleted_on_apply(self):
        segment = self._manual_segment(start=0, end=2)
        stale = ImageClassificationAnnotation.objects.create(
            frame=self.frames[4],
            label=self.label,
            value=True,
            information_source=self.manual_source,
            external_annotation_id=segment_derived_external_annotation_id(
                segment_id=segment.pk,
                frame_id=self.frames[4].pk,
                label_id=self.label.pk,
                information_source_id=self.manual_source.pk,
                model_meta_id=None,
            ),
        )

        report = reconcile_frame_segment_annotations(
            FrameSegmentReconciliationSpec(
                video_ids=(self.video.pk,),
                apply=True,
            )
        )

        self.assertEqual(report.summary.stale_generated_annotations, 1)
        self.assertEqual(report.summary.deleted_stale_generated_annotations, 1)
        self.assertFalse(
            ImageClassificationAnnotation.objects.filter(pk=stale.pk).exists()
        )

    def test_stale_unmarked_annotation_is_reported_but_not_deleted(self):
        self._manual_segment(start=0, end=2)
        unmarked = ImageClassificationAnnotation.objects.create(
            frame=self.frames[4],
            label=self.label,
            value=True,
            information_source=self.manual_source,
        )

        report = reconcile_frame_segment_annotations(
            FrameSegmentReconciliationSpec(
                video_ids=(self.video.pk,),
                apply=True,
            )
        )

        self.assertEqual(report.summary.suspicious_unmarked_annotations, 1)
        self.assertTrue(
            ImageClassificationAnnotation.objects.filter(pk=unmarked.pk).exists()
        )

    def test_no_label_and_no_frame_segments_are_counted_as_skipped(self):
        LabelVideoSegment.objects.create(
            video_file=self.video,
            label=None,
            source=self.manual_source,
            start_frame_number=0,
            end_frame_number=1,
        )
        LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.other_label,
            source=self.manual_source,
            start_frame_number=100,
            end_frame_number=101,
        )

        report = reconcile_frame_segment_annotations(
            FrameSegmentReconciliationSpec(video_ids=(self.video.pk,))
        )

        self.assertEqual(report.summary.skipped_no_label, 1)
        self.assertEqual(report.summary.skipped_no_frames, 1)

    def test_annotator_scoped_repair_does_not_touch_other_tracks(self):
        segment = self._manual_segment(start=0, end=2)
        bob_stale = ImageClassificationAnnotation.objects.create(
            frame=self.frames[4],
            label=self.label,
            value=True,
            information_source=self.manual_source,
            annotator="bob",
            external_annotation_id=segment_derived_external_annotation_id(
                segment_id=segment.pk,
                frame_id=self.frames[4].pk,
                label_id=self.label.pk,
                information_source_id=self.manual_source.pk,
                model_meta_id=None,
                annotator="bob",
            ),
        )

        report = reconcile_frame_segment_annotations(
            FrameSegmentReconciliationSpec(
                video_ids=(self.video.pk,),
                annotator="alice",
                apply=True,
            )
        )

        self.assertEqual(report.summary.created_annotations, 2)
        self.assertEqual(report.summary.stale_generated_annotations, 0)
        self.assertEqual(
            ImageClassificationAnnotation.objects.filter(annotator="alice").count(),
            2,
        )
        self.assertTrue(
            ImageClassificationAnnotation.objects.filter(pk=bob_stale.pk).exists()
        )

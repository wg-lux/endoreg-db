from unittest.mock import patch

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
from endoreg_db.models.state.frame_annotation import (
    FrameAnnotationQueueSpec,
    FrameAnnotationStatus,
    FrameSamplingStrategy,
    build_frame_task_queue,
    mark_frame_annotations_generated,
    mark_frame_prediction_completed,
    mark_frame_prediction_reset,
    mark_prediction_segments_created,
    resolve_frame_annotation_status,
    resolve_ai_dataset_for_queue,
    segment_derived_external_annotation_id,
)


class FrameAnnotationStateTest(TestCase):
    def setUp(self):
        self.center = Center.objects.create(name="frame-state-center")
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="frame-state-video",
            original_file_name="frame_state.mp4",
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
        self.unextracted_frame = Frame.objects.create(
            video=self.video,
            frame_number=99,
            relative_path="frame_0000099.jpg",
            is_extracted=False,
        )
        self.manual_source = InformationSource.objects.create(name="manual_annotation")
        self.prediction_source = InformationSource.objects.create(name="prediction")
        self.target_label = Label.objects.create(name="frame-state-target")
        self.filter_label = Label.objects.create(name="frame-state-filter")
        self.segment_label = Label.objects.create(name="frame-state-segment")
        self.label_set = LabelSet.objects.create(
            name="frame-state-label-set",
            version=1,
        )
        self.label_set.labels.add(
            self.target_label,
            self.filter_label,
            self.segment_label,
        )

    def test_status_mutators_preserve_existing_video_state_fields(self):
        self.video.get_or_create_state()
        self.assertEqual(
            resolve_frame_annotation_status(self.video),
            FrameAnnotationStatus.FRAMES_UNAVAILABLE.value,
        )

        state = self.video.get_or_create_state()
        state.frames_extracted = True
        state.save(update_fields=["frames_extracted"])

        self.assertEqual(
            resolve_frame_annotation_status(self.video),
            FrameAnnotationStatus.PREDICTION_PENDING.value,
        )

        mark_frame_prediction_completed(self.video)
        self.assertEqual(
            resolve_frame_annotation_status(self.video),
            FrameAnnotationStatus.PREDICTION_READY.value,
        )

        mark_prediction_segments_created(self.video, created=True)
        self.assertEqual(
            resolve_frame_annotation_status(self.video),
            FrameAnnotationStatus.ANNOTATION_READY.value,
        )

        mark_frame_annotations_generated(self.video)
        self.assertEqual(
            resolve_frame_annotation_status(self.video),
            FrameAnnotationStatus.ANNOTATION_COMPLETE.value,
        )

        mark_frame_prediction_reset(self.video)
        self.assertEqual(
            resolve_frame_annotation_status(self.video),
            FrameAnnotationStatus.PREDICTION_PENDING.value,
        )

    def test_plain_queue_uses_extracted_frames_only(self):
        spec = FrameAnnotationQueueSpec(
            limit=10,
            video_id=self.video.pk,
            information_source_name=self.manual_source.name,
            sampling_strategy=FrameSamplingStrategy.NONE,
            exclude_annotated=False,
        )

        with patch(
            "endoreg_db.models.state.frame_annotation.random.randint",
            return_value=0,
        ):
            result = build_frame_task_queue(spec)

        frame_ids = {task["frame_id"] for task in result.tasks}
        self.assertEqual(frame_ids, {frame.pk for frame in self.frames})
        self.assertNotIn(self.unextracted_frame.pk, frame_ids)

    def test_filtered_queue_selects_frames_with_positive_filter_label(self):
        ImageClassificationAnnotation.objects.create(
            frame=self.frames[2],
            label=self.filter_label,
            value=True,
            information_source=self.prediction_source,
            annotator="model",
        )

        spec = FrameAnnotationQueueSpec(
            limit=1,
            video_id=self.video.pk,
            label_set=self.label_set,
            target_label=self.target_label,
            filter_label=self.filter_label,
            information_source_name=self.manual_source.name,
            sampling_strategy=FrameSamplingStrategy.NONE,
            annotator="alice",
        )

        result = build_frame_task_queue(spec)

        self.assertEqual(result.tasks[0]["frame_id"], self.frames[2].pk)
        self.assertEqual(
            {label["id"] for label in result.tasks[0]["label_options"]},
            {self.target_label.pk, self.filter_label.pk, self.segment_label.pk},
        )

    def test_frame_and_segment_annotation_properties_use_runtime_source_names(self):
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.target_label,
            source=self.manual_source,
            start_frame_number=self.frames[0].frame_number,
            end_frame_number=self.frames[2].frame_number + 1,
        )
        prediction_source = InformationSource.objects.create(
            name="prediction_annotation"
        )
        ImageClassificationAnnotation.objects.create(
            frame=self.frames[0],
            label=self.target_label,
            value=True,
            information_source=self.manual_source,
            annotator="alice",
        )
        ImageClassificationAnnotation.objects.create(
            frame=self.frames[1],
            label=self.target_label,
            value=True,
            information_source=prediction_source,
            annotator="pipe-1",
        )

        self.assertEqual(segment.manual_frame_annotations.count(), 1)
        self.assertEqual(segment.frame_predictions.count(), 1)
        self.assertEqual(self.frames[0].manual_annotations.count(), 1)
        self.assertEqual(self.frames[1].predictions.count(), 1)

    def test_direct_manual_frame_annotation_overrides_segment_generated_positive(self):
        frontend_source = InformationSource.objects.create(
            name="frame_annotation_frontend"
        )
        segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.target_label,
            source=self.manual_source,
            start_frame_number=self.frames[0].frame_number,
            end_frame_number=self.frames[0].frame_number + 1,
        )
        ImageClassificationAnnotation.objects.create(
            frame=self.frames[0],
            label=self.target_label,
            value=True,
            information_source=self.manual_source,
            external_annotation_id=segment_derived_external_annotation_id(
                segment_id=segment.pk,
                frame_id=self.frames[0].pk,
                label_id=self.target_label.pk,
                information_source_id=self.manual_source.pk,
                model_meta_id=None,
            ),
        )
        ImageClassificationAnnotation.objects.create(
            frame=self.frames[0],
            label=self.target_label,
            value=False,
            information_source=frontend_source,
        )

        spec = FrameAnnotationQueueSpec(
            limit=1,
            video_id=self.video.pk,
            label_set=self.label_set,
            target_label=self.target_label,
            information_source_name=self.manual_source.name,
            sampling_strategy=FrameSamplingStrategy.NONE,
            exclude_annotated=False,
        )

        with patch(
            "endoreg_db.models.state.frame_annotation.random.randint",
            return_value=0,
        ):
            result = build_frame_task_queue(spec)

        self.assertEqual(result.tasks[0]["frame_id"], self.frames[0].pk)
        self.assertEqual(result.tasks[0]["manual_positive_label_ids"], [])
        self.assertEqual(result.tasks[0]["suggested_label_ids"], [])

    def test_exclude_annotated_is_scoped_to_target_label_and_annotator(self):
        ImageClassificationAnnotation.objects.create(
            frame=self.frames[0],
            label=self.target_label,
            value=True,
            information_source=self.manual_source,
            annotator="alice",
        )

        alice_spec = FrameAnnotationQueueSpec(
            limit=1,
            video_id=self.video.pk,
            target_label=self.target_label,
            information_source_name=self.manual_source.name,
            sampling_strategy=FrameSamplingStrategy.NONE,
            annotator="alice",
        )
        bob_spec = FrameAnnotationQueueSpec(
            limit=1,
            video_id=self.video.pk,
            target_label=self.target_label,
            information_source_name=self.manual_source.name,
            sampling_strategy=FrameSamplingStrategy.NONE,
            annotator="bob",
        )

        with patch(
            "endoreg_db.models.state.frame_annotation.random.randint",
            return_value=0,
        ):
            alice_result = build_frame_task_queue(alice_spec)
            bob_result = build_frame_task_queue(bob_spec)

        self.assertEqual(alice_result.tasks[0]["frame_id"], self.frames[1].pk)
        self.assertEqual(bob_result.tasks[0]["frame_id"], self.frames[0].pk)

    def test_dataset_annotation_sampling_strategy_is_configurable(self):
        dataset = AIDataSet.objects.create(
            name="frame-state-image-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )
        annotation = ImageClassificationAnnotation.objects.create(
            frame=self.frames[1],
            label=self.target_label,
            value=True,
            information_source=self.manual_source,
            annotator="dataset",
        )
        dataset.image_annotations.add(annotation)

        spec = FrameAnnotationQueueSpec(
            limit=1,
            video_id=self.video.pk,
            label_set=self.label_set,
            information_source_name=self.manual_source.name,
            ai_dataset=dataset,
            sampling_strategy=FrameSamplingStrategy.ANNOTATIONS,
            exclude_annotated=False,
        )

        result = build_frame_task_queue(spec)

        self.assertEqual(result.selection_strategy, "dataset_annotations")
        self.assertEqual(result.tasks[0]["frame_id"], self.frames[1].pk)
        self.assertEqual(
            result.annotation_bucket_counts,
            {str(self.target_label.pk): 1},
        )

    def test_dataset_segment_sampling_strategy_filters_prediction_segments(self):
        dataset = AIDataSet.objects.create(
            name="frame-state-video-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_VIDEO,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_VIDEO_SEGMENT_CLASSIFICATION,
        )
        prediction_segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.segment_label,
            source=self.prediction_source,
            start_frame_number=self.frames[3].frame_number,
            end_frame_number=self.frames[3].frame_number + 1,
        )
        manual_segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.filter_label,
            source=self.manual_source,
            start_frame_number=self.frames[0].frame_number,
            end_frame_number=self.frames[0].frame_number + 1,
        )
        dataset.video_annotations.add(prediction_segment, manual_segment)

        spec = FrameAnnotationQueueSpec(
            limit=1,
            video_id=self.video.pk,
            label_set=self.label_set,
            information_source_name=self.manual_source.name,
            ai_dataset=dataset,
            sampling_strategy=FrameSamplingStrategy.SEGMENTS,
            prediction_segments_only=True,
            exclude_annotated=False,
        )

        result = build_frame_task_queue(spec)

        self.assertEqual(result.selection_strategy, "dataset_segments")
        self.assertEqual(result.tasks[0]["frame_id"], self.frames[3].pk)
        self.assertEqual(
            result.segment_bucket_counts,
            {str(self.segment_label.pk): 1},
        )

    def test_phi_dataset_queue_requires_video_raw_file(self):
        dataset = AIDataSet.objects.create(
            name="phi-frame-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type="phi_region_detector",
        )
        raw_video = VideoFile.objects.create(
            center=self.center,
            video_hash="frame-state-video-raw",
            original_file_name="frame_state_raw.mp4",
            fps=25.0,
            frame_count=2,
        )
        raw_video.raw_file.name = "sensitive_videos/phi-raw.mp4"
        raw_video.save(update_fields=["raw_file"])
        raw_frame = Frame.objects.create(
            video=raw_video,
            frame_number=0,
            relative_path="frame_0000000.jpg",
            is_extracted=True,
        )
        rawless_frame = Frame.objects.create(
            video=self.video,
            frame_number=5,
            relative_path="frame_0000005.jpg",
            is_extracted=True,
        )
        dataset.image_annotations.add(
            ImageClassificationAnnotation.objects.create(
                frame=raw_frame,
                label=self.target_label,
                value=True,
                information_source=self.manual_source,
                annotator="dataset",
            ),
            ImageClassificationAnnotation.objects.create(
                frame=rawless_frame,
                label=self.target_label,
                value=True,
                information_source=self.manual_source,
                annotator="dataset",
            ),
        )

        spec = FrameAnnotationQueueSpec(
            limit=10,
            information_source_name=self.manual_source.name,
            ai_dataset=dataset,
            sampling_strategy=FrameSamplingStrategy.NONE,
            exclude_annotated=False,
        )

        with patch(
            "endoreg_db.models.state.frame_annotation.random.randint",
            return_value=0,
        ):
            result = build_frame_task_queue(spec)

        frame_ids = {task["frame_id"] for task in result.tasks}
        self.assertIn(raw_frame.pk, frame_ids)
        self.assertNotIn(rawless_frame.pk, frame_ids)

    def test_resolve_ai_dataset_for_queue_falls_back_to_first_dataset(self):
        dataset = AIDataSet.objects.create(
            name="default-frame-dataset",
            dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
            ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
        )

        resolved = resolve_ai_dataset_for_queue(
            dataset_name_raw=None,
            dataset_type_raw=None,
        )

        self.assertEqual(resolved, dataset)

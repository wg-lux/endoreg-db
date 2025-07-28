from django.test import TestCase
from endoreg_db.models import (
    LabelVideoSegment,
    Frame,
    Label,
    InformationSource,
    VideoPredictionMeta,
    ImageClassificationAnnotation,
)

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.serializers import LabelVideoSegmentSerializer

import logging
from ..helpers.data_loader import (
    load_ai_model_label_data,
    load_ai_model_data,
    load_default_ai_model
)
from ..helpers.default_objects import (
    get_latest_segmentation_model,
    get_default_video_file,
    get_information_source_prediction
)

logger = logging.getLogger(__name__)

class LabelVideoSegmentModelTest(TestCase):
    def setUp(self):
        load_ai_model_label_data()
        load_ai_model_data()
        load_default_ai_model()

        self.ai_model_meta = get_latest_segmentation_model()
        self.video_file = get_default_video_file()
        self.assertIsNotNone(self.video_file, "VideoFile should be created")
        self.assertIsNotNone(self.video_file.frame_count, "VideoFile frame_count should be set")
        self.assertTrue(self.video_file.frames.exists(), "Frame objects should be initialized")

        self.outside_label = Label.get_outside_label()

        self.source_prediction = get_information_source_prediction()

        self.prediction_meta, _ = VideoPredictionMeta.objects.get_or_create(
            video_file=self.video_file, model_meta=self.ai_model_meta
        )

        self.start_frame = 10
        self.end_frame = min(self.start_frame + 20, self.video_file.frame_count)
        self.assertLess(self.start_frame, self.end_frame+1, "Start frame must be less than end frame")
        self.segment = LabelVideoSegment.create_from_video(
            source=self.video_file,
            prediction_meta=self.prediction_meta,
            label=self.outside_label,
            start_frame_number=self.start_frame,
            end_frame_number=self.end_frame,
        )
        # Explicitly set the correct InformationSource for the segment
        self.segment.source = self.source_prediction
        self.segment.save()

        self.video_file.refresh_from_db()
        self.assertIsNotNone(self.segment, "LabelVideoSegment should be created")
        self.assertTrue(LabelVideoSegment.objects.filter(pk=self.segment.pk).exists(), "Segment should exist in DB after creation in setUp")

        self.segment_frame_count = self.segment.end_frame_number - self.segment.start_frame_number

    def test_create_label_video_segment(self):
        """Test creating a LabelVideoSegment and automatic state creation."""
        self.assertIsNotNone(self.segment)
        self.segment.refresh_from_db()
        try:
            self.assertIsNotNone(self.segment.state)
        except LabelVideoSegment.state.RelatedObjectDoesNotExist:
            self.fail("LabelVideoSegment state was not created automatically.")

    def test_get_frames(self):
        """Test retrieving frames associated with the segment."""
        frames = self.segment.get_frames()
        frames_list = list(frames)
        self.assertEqual(len(frames_list), self.segment_frame_count)
        self.assertIsInstance(frames_list, list)
        self.assertTrue(all(isinstance(frame, Frame) for frame in frames_list))
        self.assertTrue(all(self.segment.start_frame_number <= frame.frame_number < self.segment.end_frame_number for frame in frames_list))

    def test_frames_not_extracted_initially(self):
        """Test that frames within the segment are not marked as extracted initially."""
        frames = self.segment.get_frames()
        self.assertGreater(frames.count(), 0, "Segment should contain frames")
        self.assertEqual(frames.filter(is_extracted=True).count(), 0, "No frames should be extracted initially")

    def test_extract_and_delete_segment_frame_files(self):
        """
        Tests extraction and deletion of frame files for a video segment.
        
        Verifies that no frames are initially extracted, successfully extracts frame files for all frames in the segment, and ensures only segment frames are marked as extracted. Confirms that extracted frame files exist on disk, then deletes the frame files and checks that no frames remain marked as extracted and the files are removed from disk. Skips the test if required video assets or FFmpeg are missing.
        """
        frames_qs = self.segment.get_frames()
        self.assertEqual(frames_qs.filter(is_extracted=True).count(), 0, "No frames should be extracted initially")

        segment_pk_before = self.segment.pk

        extract_success = False
        try:
            extract_success = self.segment.extract_segment_frame_files(overwrite=True)
            self.assertTrue(extract_success, "extract_segment_frame_files should return True on success")
        except FileNotFoundError as e:
            self.skipTest(f"Skipping frame file test: FFmpeg not found or video asset missing? ({e})")
        except RuntimeError as e:
            self.fail(f"Frame extraction failed: {e}")
        except Exception as e:
            self.fail(f"Unexpected error during frame extraction: {e}")

        logger.info(f"Segment PK before refresh: {segment_pk_before}")
        segment_exists = LabelVideoSegment.objects.filter(pk=segment_pk_before).exists()
        logger.info(f"Does segment {segment_pk_before} exist in DB before refresh? {segment_exists}")
        if not segment_exists:
            self.fail(f"Segment with PK {segment_pk_before} was deleted during extract_segment_frame_files call.")

        try:
            self.segment.refresh_from_db()
        except LabelVideoSegment.DoesNotExist:
            self.fail(f"Segment PK {segment_pk_before} exists in DB but refresh_from_db failed (transaction issue?).")
        except Exception as e:
            self.fail(f"refresh_from_db failed with unexpected error: {e}")

        self.video_file.refresh_from_db()
        frames_after_extract = self.segment.get_frames().order_by('frame_number')
        self.assertEqual(frames_after_extract.count(), self.segment_frame_count, "Should still have the same number of frames")
        extracted_count = frames_after_extract.filter(is_extracted=True).count()
        self.assertEqual(extracted_count, self.segment_frame_count, "All frames in the segment should now be marked as extracted")

        outside_frames_before = self.video_file.frames.filter(frame_number__lt=self.segment.start_frame_number)
        outside_frames_after = self.video_file.frames.filter(frame_number__gte=self.segment.end_frame_number)
        self.assertEqual(outside_frames_before.filter(is_extracted=True).count(), 0, "Frames before the segment should not be extracted")
        self.assertEqual(outside_frames_after.filter(is_extracted=True).count(), 0, "Frames after the segment should not be extracted")

        sample_frame = frames_after_extract.first()
        if sample_frame:
            self.assertTrue(sample_frame.file_path.exists(), f"Frame file {sample_frame.file_path} should exist after extraction")
        else:
            self.fail("Could not get a sample frame after extraction.")

        try:
            self.segment.delete_frame_files()
        except Exception as e:
            self.fail(f"Unexpected error during frame deletion: {e}")

        frames_after_delete = self.segment.get_frames()
        self.assertEqual(frames_after_delete.filter(is_extracted=True).count(), 0, "No frames in the segment should be extracted after deletion")

        if sample_frame:
            sample_frame.refresh_from_db()
            self.assertFalse(sample_frame.file_path.exists(), f"Frame file {sample_frame.file_path} should NOT exist after deletion")
    
    def test_create_segment_with_video_seg_label_name(self):
        """
        Tests creating a LabelVideoSegment using a serializer with a label name and video ID.
        
        Verifies that the serializer validates the input data, successfully creates a segment, and assigns a Label instance to the segment.
        """
        v = self.video_file
        data = {
            "video_id": v.id, 
            "label_name": "appendix",
            "start_time": self.start_frame / v.fps, 
            "end_time": self.end_frame / v.fps
        }
        s = LabelVideoSegmentSerializer(data=data)
        assert s.is_valid(), s.errors
        segment = s.save()
        assert isinstance(segment.label, Label)

    def test_get_segment_len_in_s(self):
        """
        Tests that the segment's duration in seconds is correctly calculated based on its frame range and the video's FPS.
        """
        expected_fps = self.video_file.fps

        expected_duration = (self.segment.end_frame_number - self.segment.start_frame_number) / expected_fps
        actual_duration = self.segment.get_segment_len_in_s()
        self.assertAlmostEqual(actual_duration, expected_duration, places=4)

    
    def test_get_frames_without_annotation(self):
        """Test retrieving frames that don't have annotations for the segment's label."""
        frames = list(self.segment.get_frames())
        self.assertGreater(len(frames), 0, "Segment should have frames")

        frames_without_anno = self.segment.get_frames_without_annotation(n_frames=self.segment_frame_count)
        self.assertEqual(len(frames_without_anno), self.segment_frame_count)
        self.assertListEqual(sorted([f.id for f in frames_without_anno]), sorted([f.id for f in frames]))

        if frames:
            first_frame = frames[0]
            ImageClassificationAnnotation.objects.create(
                frame=first_frame,
                label=self.segment.label,
                model_meta=self.prediction_meta.model_meta,
                value=True,
                information_source=self.source_prediction
            )

            frames_without_anno_after = self.segment.get_frames_without_annotation(n_frames=self.segment_frame_count)
            self.assertEqual(len(frames_without_anno_after), self.segment_frame_count - 1)
            self.assertNotIn(first_frame.id, [f.id for f in frames_without_anno_after])

            frames_without_anno_limited = self.segment.get_frames_without_annotation(n_frames=1)
            self.assertEqual(len(frames_without_anno_limited), 1)
            self.assertNotIn(first_frame.id, [f.id for f in frames_without_anno_limited])

    def test_generate_annotations(self):
        """Test generating ImageClassificationAnnotations for segment frames."""
        # Corrected filter using frame_id__in and the correct FK field name 'video'
        relevant_frame_ids = Frame.objects.filter(video=self.video_file).values_list('id', flat=True)
        initial_annotation_count = ImageClassificationAnnotation.objects.filter(
            frame_id__in=relevant_frame_ids,
            label=self.segment.label
        ).count()
        self.assertEqual(initial_annotation_count, 0)

        # Ensure the segment source is correctly set before generating annotations
        self.segment.refresh_from_db() # Refresh to be sure
        self.assertEqual(self.segment.source, self.source_prediction, "Segment source should be set to source_prediction")

        self.segment.generate_annotations()

        # Check if annotations were created with the correct source
        final_annotation_count = ImageClassificationAnnotation.objects.filter(
            frame__video=self.video_file,
            label=self.segment.label,
            model_meta=self.prediction_meta.model_meta,
            information_source=self.source_prediction,
            value=True
        ).count()
        self.assertEqual(final_annotation_count, self.segment_frame_count)

        self.segment.generate_annotations() # Idempotency check
        idempotent_count = ImageClassificationAnnotation.objects.filter(
            frame__video=self.video_file,
            label=self.segment.label,
            model_meta=self.prediction_meta.model_meta,
            information_source=self.source_prediction
        ).count()
        self.assertEqual(idempotent_count, self.segment_frame_count)

    def test_get_annotations(self):
        """Test retrieving annotations associated with the segment."""
        self.assertEqual(self.segment.all_frame_annotations.count(), 0)

        self.segment.generate_annotations()

        segment_annotations = self.segment.all_frame_annotations
        self.assertEqual(segment_annotations.count(), self.segment_frame_count)

        manual_annotations = ImageClassificationAnnotation.objects.filter(
            frame__video=self.video_file,
            frame__frame_number__gte=self.segment.start_frame_number,
            frame__frame_number__lt=self.segment.end_frame_number,
            label=self.segment.label
        )
        self.assertQuerySetEqual(segment_annotations.order_by('pk'), manual_annotations.order_by('pk'))

    def test_lvs_serializer_base(self) -> None:
        """
        Test serialization of the LabelVideoSegment.
        Tests whether the following fields are present in the serialized data:
        - id
        - video_id
        - label_id
        - start_frame_number
        - end_frame_number
        """
        serializer = LabelVideoSegmentSerializer(instance=self.segment)
        
        data = serializer.to_representation(self.segment)

        # data =serializer.validate(serializer.data)

        self.assertIn('id', data)
        self.assertIn('video_id', data)
        self.assertIn('label_id', data)
        self.assertIn('start_frame_number', data)
        self.assertIn('end_frame_number', data)

        label = self.segment.label

        self.assertEqual(data['label_id'], label.pk)

    def test_lvs_serializer_create_method(self) -> None:
        """
        Test creating a new LabelVideoSegment using the serializer.
        Tests whether the serializer can create a new segment with the correct fields.
        """
        label = Label.objects.first()
        label_id = label.pk
        frame_count = self.video_file.frame_count
        data = {
            'video_id': self.video_file.pk,
            'label_id': label_id,
            'start_time': 0,
            'end_time': frame_count / self.video_file.fps,
        }
        
        serializer = LabelVideoSegmentSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

        segment = serializer.create(serializer.validated_data)
        print(f"Created segment: {segment}")
        self.assertIsNotNone(segment)
        self.assertIsInstance(segment, LabelVideoSegment)
        self.assertIsInstance(segment.video_file, VideoFile)
        self.assertIsInstance(segment.label, Label)
        self.assertIsInstance(segment.source, InformationSource)
        self.assertIsInstance(segment.start_frame_number, int)
        self.assertIsInstance(segment.end_frame_number, int)

    def test_lvs_serializer_create_method_exceed_frame_limit(self) -> None:
        """
        Test that creating a new LabelVideoSegment with end_frame_number exceeding video frame count raises ValidationError.
        """
        from rest_framework import serializers
        label = Label.objects.first()
        label_id = label.pk
        frame_count = self.video_file.frame_count + 10  # Intentionally exceed frame count
        data = {
            'video_id': self.video_file.pk,
            'label_id': label_id,
            'start_time': 0,
            'end_time': frame_count / self.video_file.fps,
        }
        serializer = LabelVideoSegmentSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        # Expect DRF ValidationError, not ValueError
        with self.assertRaises(serializers.ValidationError) as cm:
            serializer.create(serializer.validated_data)
        self.assertIn("exceeds video frame count", str(cm.exception))

    def tearDown(self):
        if hasattr(self, 'video_file') and self.video_file:
            try:
                frame_dir = self.video_file.get_frame_dir_path()
                if frame_dir and frame_dir.exists():
                    import shutil
                    shutil.rmtree(frame_dir, ignore_errors=True)
                    logger.info("Cleaned up frame directory: %s", frame_dir)
            except Exception as e:
                logger.warning("Error during frame directory cleanup: %s", e)
            self.video_file.delete_with_file()

from django.test import TestCase
from scipy.constants import value

# Adjust imports based on your actual project structure
from endoreg_db.models import (
    VideoFile,
    Label,
    LabelVideoSegment,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Center,
)
from endoreg_db.serializers import LabelVideoSegmentSerializer


class LabelVideoSegmentSerializerTest(TestCase):
    def setUp(self):
        # 1. Setup minimal dependencies
        self.center = Center.objects.create(name="Test Center")
        self.source = InformationSource.objects.create(name="Manual Annotation")

        # 2. Create a Video with known FPS (30.0 makes math easy)
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash="test_hash_123",
            original_file_name="test_video.mp4",
            fps=30.0,
            frame_count=1000,
            duration=33.33,
        )

        # 3. Create a Label
        self.label_polyp = Label.objects.create(name="polyp")

        # 4. Create a dummy segment
        self.segment = LabelVideoSegment.objects.create(
            video_file=self.video,
            label=self.label_polyp,
            start_frame_number=0,
            end_frame_number=30,  # 1 second
            source=self.source,
        )

    def test_update_time_precision_from_frontend_drag(self):
        """
        Scenario: User drags a handle in Timeline.vue.
        videoStore.ts -> updateSegmentAPI sends 'start_time' as a float.

        Mathematical Edge Case:
        Frame 7 at 30 FPS is exactly 0.2333333333 seconds.
        0.2333333333 * 30.0 = 6.999999999.

        If backend uses int(), it becomes Frame 6 (WRONG).
        If backend uses round(), it becomes Frame 7 (CORRECT).
        """
        target_frame = 7
        fps = 30.0

        # Simulate time sent from frontend (float precision)
        calculated_time = target_frame / fps

        payload = {
            "start_time": calculated_time,
            # Frontend might send end_time same as old one, but required by logic validation
            "end_time": self.segment.end_time,
        }

        serializer = LabelVideoSegmentSerializer(
            instance=self.segment, data=payload, partial=True
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        updated_segment = serializer.save()

        # EXPECTATION: The serializer correctly rounded the float back to the integer frame
        self.assertEqual(updated_segment.start_frame_number, 7)


    def test_video_store_creation_payload(self):
        """
        Scenario: videoStore.ts -> createSegment
        The store calculates frames on the client side using Math.floor and sends frame numbers directly.
        """
        payload = {
            "video_id": self.video.pk,
            "label_id": self.label_polyp.pk,
            # Frontend sends calculated frames
            "start_frame_number": 90,
            "end_frame_number": 120,
        }

        serializer = LabelVideoSegmentSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        new_segment = serializer.save()

        self.assertEqual(new_segment.start_frame_number, 90)
        self.assertEqual(new_segment.end_frame_number, 120)
        self.assertEqual(new_segment.video_file, self.video)

    def test_performance_get_time_segments_n_plus_one(self):
        """
        Scenario: Loading the Timeline.
        The serializer method `get_time_segments` iterates over frames.
        We must ensure it doesn't fire a DB query for every single frame to get annotations.
        """
        # 1. Create 50 frames for the segment [0-50]
        # (Assuming your Frame model is linked to VideoFile and has frame_number)
        frames = []
        for i in range(50):
            frames.append(
                Frame(video=self.video, frame_number=i)
            )
        Frame.objects.bulk_create(frames)

        # 2. Update segment to cover these frames
        self.segment.end_frame_number = 50
        self.segment.save()

        # 3. Create Annotations for these frames (to trigger the potential N+1 fetch)
        # We need to fetch these to prove we aren't querying 50 times
        saved_frames = Frame.objects.filter(video=self.video)
        annotations = []
        for frame in saved_frames:
            annotations.append(
                ImageClassificationAnnotation(
                    frame=frame,
                    label=self.label_polyp,
                    information_source=self.source,
                    value=True,
                )
            )
        ImageClassificationAnnotation.objects.bulk_create(annotations)
        if frames:
            _ = frames[0].file_path

        # 4. Measure Queries
        # Expected Queries:
        # 1. Fetch Segment
        # 2. Fetch Frames (filtered by segment range)
        # 3. Prefetch Annotations (1 query for all frames)
        # Total should be low single digits, NOT 50+.

        with self.assertNumQueries(2):
            # Note: The exact number depends on how `get_time_segments` is implemented.
            # If using `prefetch_related` inside the method, it might be 2 or 3.
            # If N+1 exists, this will be 52+.

            serializer = LabelVideoSegmentSerializer(self.segment)
            # We explicitly call the method to trigger the logic
            data = serializer.get_time_segments(self.segment)

            # Verify data integrity just in case
            self.assertEqual(len(data["frames"]), 50)
            self.assertTrue(len(data["frames"][0]["all_classifications"]) > 0)

"""
Unit tests for segment update logic after frame removal.

Tests the update_segments_after_frame_removal() function from Phase 1.4.
"""
import pytest
from django.test import TestCase
from endoreg_db.models import VideoFile, LabelVideoSegment, Label, VideoState
from endoreg_db.views.video.correction import update_segments_after_frame_removal


class SegmentUpdateAfterFrameRemovalTest(TestCase):
    """Test segment boundary updates after frame removal."""
    
    def setUp(self):
        """Create test video and segments."""
        # Create video
        self.video = VideoFile.objects.create(
            video_hash="test_hash_segment_update",
            original_file_name="test_video.mp4"
        )
        
        # Create video state
        self.video_state = VideoState.objects.create(
            video_file=self.video,
            frames_extracted=True
        )
        
        # Create test labels
        self.label1 = Label.objects.create(name="Polyp")
        self.label2 = Label.objects.create(name="Normal")
    
    def test_no_frames_removed(self):
        """Test that segments remain unchanged when no frames are removed."""
        # Create segment
        segment = LabelVideoSegment.objects.create(
            video=self.video,
            start_frame=100,
            end_frame=200,
            label=self.label1
        )
        
        # Remove no frames
        result = update_segments_after_frame_removal(self.video, [])
        
        # Verify no changes
        segment.refresh_from_db()
        assert segment.start_frame == 100
        assert segment.end_frame == 200
        assert result['segments_updated'] == 0
        assert result['segments_deleted'] == 0
        assert result['segments_unchanged'] == 0
    
    def test_frames_removed_before_segment(self):
        """Test segment shift when frames are removed before it."""
        # Create segment at frames 100-200
        segment = LabelVideoSegment.objects.create(
            video=self.video,
            start_frame=100,
            end_frame=200,
            label=self.label1
        )
        
        # Remove frames 10, 20, 30 (all before segment)
        removed_frames = [10, 20, 30]
        result = update_segments_after_frame_removal(self.video, removed_frames)
        
        # Verify segment shifted left by 3 frames
        segment.refresh_from_db()
        assert segment.start_frame == 97  # 100 - 3
        assert segment.end_frame == 197   # 200 - 3
        assert result['segments_updated'] == 1
        assert result['segments_deleted'] == 0
    
    def test_frames_removed_within_segment(self):
        """Test segment shrinkage when frames are removed within it."""
        # Create segment at frames 100-200
        segment = LabelVideoSegment.objects.create(
            video=self.video,
            start_frame=100,
            end_frame=200,
            label=self.label1
        )
        
        # Remove frames 120, 150, 180 (all within segment)
        removed_frames = [120, 150, 180]
        result = update_segments_after_frame_removal(self.video, removed_frames)
        
        # Verify segment shrunk by 3 frames
        segment.refresh_from_db()
        assert segment.start_frame == 100  # No shift (no frames before)
        assert segment.end_frame == 197    # 200 - 3 (frames within)
        assert result['segments_updated'] == 1
        assert result['segments_deleted'] == 0
    
    def test_frames_removed_before_and_within_segment(self):
        """Test segment update when frames removed both before and within."""
        # Create segment at frames 100-200
        segment = LabelVideoSegment.objects.create(
            video=self.video,
            start_frame=100,
            end_frame=200,
            label=self.label1
        )
        
        # Remove frames: 50, 75 (before), 120, 150, 180 (within)
        removed_frames = [50, 75, 120, 150, 180]
        result = update_segments_after_frame_removal(self.video, removed_frames)
        
        # Verify both shift and shrinkage
        segment.refresh_from_db()
        assert segment.start_frame == 98   # 100 - 2 (frames before)
        assert segment.end_frame == 195    # 200 - 2 (before) - 3 (within)
        assert result['segments_updated'] == 1
        assert result['segments_deleted'] == 0
    
    def test_segment_completely_removed(self):
        """Test segment deletion when all its frames are removed."""
        # Create small segment at frames 100-105
        segment = LabelVideoSegment.objects.create(
            video=self.video,
            start_frame=100,
            end_frame=105,
            label=self.label1
        )
        
        # Remove all frames in segment
        removed_frames = [100, 101, 102, 103, 104, 105]
        result = update_segments_after_frame_removal(self.video, removed_frames)
        
        # Verify segment was deleted
        assert not LabelVideoSegment.objects.filter(id=segment.id).exists()
        assert result['segments_updated'] == 0
        assert result['segments_deleted'] == 1
    
    def test_multiple_segments_mixed_updates(self):
        """Test multiple segments with different update scenarios."""
        # Segment 1: frames 50-100 (will be shifted)
        seg1 = LabelVideoSegment.objects.create(
            video=self.video,
            start_frame=50,
            end_frame=100,
            label=self.label1
        )
        
        # Segment 2: frames 150-200 (will be shifted and shrunk)
        seg2 = LabelVideoSegment.objects.create(
            video=self.video,
            start_frame=150,
            end_frame=200,
            label=self.label2
        )
        
        # Segment 3: frames 250-260 (will be deleted)
        seg3 = LabelVideoSegment.objects.create(
            video=self.video,
            start_frame=250,
            end_frame=260,
            label=self.label1
        )
        
        # Remove frames: 10,20 (before seg1), 170,180 (within seg2), 250-260 (all of seg3)
        removed_frames = [10, 20, 170, 180, 250, 251, 252, 253, 254, 255, 256, 257, 258, 259, 260]
        result = update_segments_after_frame_removal(self.video, removed_frames)
        
        # Verify results
        seg1.refresh_from_db()
        assert seg1.start_frame == 48   # 50 - 2 (frames before)
        assert seg1.end_frame == 98     # 100 - 2 (frames before)
        
        seg2.refresh_from_db()
        assert seg2.start_frame == 148  # 150 - 2 (frames before seg1)
        assert seg2.end_frame == 196    # 200 - 2 (before) - 2 (within)
        
        # Segment 3 should be deleted
        assert not LabelVideoSegment.objects.filter(id=seg3.id).exists()
        
        assert result['segments_updated'] == 2  # seg1 and seg2
        assert result['segments_deleted'] == 1  # seg3
    
    def test_frames_removed_after_segment(self):
        """Test that removing frames after a segment doesn't affect it."""
        # Create segment at frames 50-100
        segment = LabelVideoSegment.objects.create(
            video=self.video,
            start_frame=50,
            end_frame=100,
            label=self.label1
        )
        
        # Remove frames 150, 200, 250 (all after segment)
        removed_frames = [150, 200, 250]
        result = update_segments_after_frame_removal(self.video, removed_frames)
        
        # Verify segment unchanged
        segment.refresh_from_db()
        assert segment.start_frame == 50
        assert segment.end_frame == 100
        assert result['segments_updated'] == 0
        assert result['segments_deleted'] == 0
        assert result['segments_unchanged'] == 1
    
    def test_duplicate_removed_frames(self):
        """Test that duplicate frame numbers are handled correctly."""
        # Create segment at frames 100-200
        segment = LabelVideoSegment.objects.create(
            video=self.video,
            start_frame=100,
            end_frame=200,
            label=self.label1
        )
        
        # Remove frames with duplicates: [50, 50, 75, 75, 75]
        removed_frames = [50, 50, 75, 75, 75]
        result = update_segments_after_frame_removal(self.video, removed_frames)
        
        # Verify only unique frames counted (50 and 75 = 2 frames)
        segment.refresh_from_db()
        assert segment.start_frame == 98   # 100 - 2
        assert segment.end_frame == 198    # 200 - 2
        assert result['segments_updated'] == 1
    
    def test_unsorted_removed_frames(self):
        """Test that unsorted frame numbers are handled correctly."""
        # Create segment at frames 100-200
        segment = LabelVideoSegment.objects.create(
            video=self.video,
            start_frame=100,
            end_frame=200,
            label=self.label1
        )
        
        # Remove frames in random order: [75, 20, 50, 10]
        removed_frames = [75, 20, 50, 10]
        result = update_segments_after_frame_removal(self.video, removed_frames)
        
        # Verify all frames counted correctly (4 frames before segment)
        segment.refresh_from_db()
        assert segment.start_frame == 96   # 100 - 4
        assert segment.end_frame == 196    # 200 - 4
        assert result['segments_updated'] == 1
    
    def test_edge_case_segment_at_frame_0(self):
        """Test segment starting at frame 0."""
        # Create segment at frames 0-50
        segment = LabelVideoSegment.objects.create(
            video=self.video,
            start_frame=0,
            end_frame=50,
            label=self.label1
        )
        
        # Remove frames 10, 20, 30 (within segment)
        removed_frames = [10, 20, 30]
        result = update_segments_after_frame_removal(self.video, removed_frames)
        
        # Verify segment shrunk but stays at frame 0
        segment.refresh_from_db()
        assert segment.start_frame == 0    # No frames before
        assert segment.end_frame == 47     # 50 - 3 (within)
        assert result['segments_updated'] == 1
    
    def test_edge_case_single_frame_segment(self):
        """Test single-frame segment (start_frame == end_frame)."""
        # Create single-frame segment at frame 100
        segment = LabelVideoSegment.objects.create(
            video=self.video,
            start_frame=100,
            end_frame=100,
            label=self.label1
        )
        
        # Remove frame 100 (the only frame in segment)
        removed_frames = [100]
        result = update_segments_after_frame_removal(self.video, removed_frames)
        
        # Verify segment deleted
        assert not LabelVideoSegment.objects.filter(id=segment.id).exists()
        assert result['segments_deleted'] == 1

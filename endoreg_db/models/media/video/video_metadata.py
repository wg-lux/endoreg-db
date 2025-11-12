"""
Video Metadata Model

Stores analysis results for videos (sensitive frames, detection statistics).
Created as part of Phase 1.1: Video Correction API Endpoints.
"""

from django.db import models

from .video_file import VideoFile


class VideoMetadata(models.Model):
    """
    Stores analysis results for videos after sensitive frame detection.

    This model holds the output of frame analysis operations (MiniCPM, OCR+LLM)
    and provides metrics for the correction UI.
    """

    video = models.OneToOneField(VideoFile, on_delete=models.CASCADE, related_name="metadata", help_text="Video file this metadata belongs to")

    # Analysis Results
    sensitive_frame_count = models.IntegerField(null=True, blank=True, help_text="Number of frames detected as containing sensitive information")
    sensitive_ratio = models.FloatField(null=True, blank=True, help_text="Ratio of sensitive frames to total frames (0.0-1.0)")
    sensitive_frame_ids = models.TextField(null=True, blank=True, help_text="JSON array of sensitive frame indices (0-based)")

    # Metadata
    analyzed_at = models.DateTimeField(auto_now=True, help_text="Timestamp of last analysis")

    class Meta:
        db_table = "video_metadata"
        verbose_name = "Video Metadata"
        verbose_name_plural = "Video Metadata"

    def __str__(self):
        return f"Metadata for {self.video.uuid} ({self.sensitive_frame_count or 0} sensitive frames)"

    @property
    def has_analysis(self) -> bool:
        """Check if this video has been analyzed."""
        return self.sensitive_frame_count is not None

    @property
    def sensitive_percentage(self) -> float:
        """Get sensitivity as percentage (0-100)."""
        if self.sensitive_ratio is not None:
            return self.sensitive_ratio * 100
        return 0.0

from __future__ import annotations

"""Video metadata model for analysis results and correction UI metrics."""

from datetime import datetime
from typing import Protocol, cast

from django.db import models

from .video_file import VideoFile


class _VideoHashSource(Protocol):
    video_hash: str


class VideoMetadata(models.Model):
    """
    Stores analysis results for videos after sensitive frame detection.

    This model holds the output of frame analysis operations (MiniCPM, OCR+LLM)
    and provides metrics for the correction UI.
    """

    objects = models.Manager["VideoMetadata"]()

    video: models.OneToOneField[VideoFile] = models.OneToOneField(
        VideoFile,
        on_delete=models.CASCADE,
        related_name="metadata",
        help_text="Video file this metadata belongs to",
    )

    # Analysis Results
    sensitive_frame_count: models.IntegerField[int | None] = models.IntegerField(
        null=True,
        blank=True,
        help_text="Number of frames detected as containing sensitive information",
    )
    sensitive_ratio: models.FloatField[float | None] = models.FloatField(
        null=True,
        blank=True,
        help_text="Ratio of sensitive frames to total frames (0.0-1.0)",
    )
    sensitive_frame_ids: models.TextField[str | None] = models.TextField(
        null=True,
        blank=True,
        help_text="JSON array of sensitive frame indices (0-based)",
    )

    # Metadata
    analyzed_at: models.DateTimeField[datetime] = models.DateTimeField(
        auto_now=True, help_text="Timestamp of last analysis"
    )

    class Meta:
        db_table = "video_metadata"
        verbose_name = "Video Metadata"
        verbose_name_plural = "Video Metadata"

    def __str__(self) -> str:
        sensitive_frame_count = self.sensitive_frame_count or 0
        video = cast(_VideoHashSource, self.video)
        return (
            f"Metadata for {video.video_hash} "
            f"({sensitive_frame_count} sensitive frames)"
        )

    @property
    def has_analysis(self) -> bool:
        """Check if this video has been analyzed."""
        return self.sensitive_frame_count is not None

    @property
    def sensitive_percentage(self) -> float:
        """Get sensitivity as percentage (0-100)."""
        sensitive_ratio = self.sensitive_ratio
        if sensitive_ratio is not None:
            return sensitive_ratio * 100.0
        return 0.0

import uuid
from django.db import models
from django.utils import timezone
from datetime import timedelta


class FrameAnonymizationRequest(models.Model):
    """Model to track frame anonymization requests."""
    
    ANONYMIZATION_LEVEL_CHOICES = [
        ("minimal", "Minimal"),
        ("faces", "Faces"),
        ("full", "Full"),
    ]
    
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]
    
    # Primary key
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Relationships
    video_file = models.ForeignKey(
        'VideoFile',
        on_delete=models.CASCADE,
        related_name='anonymization_requests'
    )
    segments = models.ManyToManyField(
        'LabelVideoSegment',
        related_name='anonymization_requests',
        blank=True
    )
    
    # Configuration
    anonymization_level = models.CharField(
        max_length=20,
        choices=ANONYMIZATION_LEVEL_CHOICES,
        default="faces"
    )
    
    # Status tracking
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending"
    )
    error_message = models.TextField(blank=True, null=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    
    # Metadata
    total_frames = models.IntegerField(default=0)
    processed_frames = models.IntegerField(default=0)
    
    class Meta:
        db_table = 'frame_anonymization_request'
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['created_at']),
            models.Index(fields=['video_file', 'status']),
        ]
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Anonymization Request {self.id} - {self.video_file.name}"
    
    @property
    def progress_percentage(self):
        """Calculate progress as percentage."""
        if self.total_frames == 0:
            return 0
        return round((self.processed_frames / self.total_frames) * 100, 2)
    
    def mark_as_processing(self):
        """Mark request as processing."""
        self.status = "processing"
        self.started_at = timezone.now()
        self.save(update_fields=['status', 'started_at'])
    
    def mark_as_completed(self):
        """Mark request as completed."""
        self.status = "completed"
        self.completed_at = timezone.now()
        self.save(update_fields=['status', 'completed_at'])
    
    def mark_as_failed(self, error_message=None):
        """Mark request as failed with optional error message."""
        self.status = "failed"
        self.error_message = error_message
        self.completed_at = timezone.now()
        self.save(update_fields=['status', 'error_message', 'completed_at'])


class AnonymousFrame(models.Model):
    """Model to store anonymized frame data."""
    
    # Primary key
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Relationships
    anonymization_request = models.ForeignKey(
        'FrameAnonymizationRequest',
        on_delete=models.CASCADE,
        related_name='anonymous_frames'
    )
    video_file = models.ForeignKey(
        'VideoFile',
        on_delete=models.CASCADE,
        related_name='anonymous_frames'
    )
    segment = models.ForeignKey(
        'LabelVideoSegment',
        on_delete=models.CASCADE,
        related_name='anonymous_frames',
        blank=True,
        null=True
    )
    
    # Frame data
    frame_number = models.IntegerField()
    timestamp_ms = models.IntegerField(help_text="Frame timestamp in milliseconds")
    
    # File paths
    original_frame_path = models.CharField(max_length=500)
    anonymized_frame_path = models.CharField(max_length=500)
    
    # Download management
    download_token = models.UUIDField(default=uuid.uuid4, unique=True)
    download_expires_at = models.DateTimeField()
    download_count = models.IntegerField(default=0)
    
    # Metadata
    file_size_bytes = models.BigIntegerField(default=0)
    anonymization_metadata = models.JSONField(
        blank=True,
        null=True,
        help_text="Metadata about anonymization process (e.g., detected faces, anonymization method)"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    last_accessed = models.DateTimeField(blank=True, null=True)
    
    class Meta:
        db_table = 'anonymous_frame'
        indexes = [
            models.Index(fields=['anonymization_request', 'frame_number']),
            models.Index(fields=['video_file', 'frame_number']),
            models.Index(fields=['download_token']),
            models.Index(fields=['download_expires_at']),
        ]
        unique_together = [
            ('anonymization_request', 'frame_number'),
        ]
        ordering = ['frame_number']
    
    def __str__(self):
        return f"Anonymous Frame {self.frame_number} - {self.video_file.name}"
    
    def save(self, *args, **kwargs):
        """Override save to set download expiration."""
        if not self.download_expires_at:
            self.download_expires_at = timezone.now() + timedelta(days=7)
        super().save(*args, **kwargs)
    
    @property
    def is_download_expired(self):
        """Check if download token has expired."""
        return timezone.now() > self.download_expires_at
    
    def refresh_download_token(self, expiry_days=7):
        """Generate new download token and extend expiry."""
        self.download_token = uuid.uuid4()
        self.download_expires_at = timezone.now() + timedelta(days=expiry_days)
        self.save(update_fields=['download_token', 'download_expires_at'])
    
    def increment_download_count(self):
        """Increment download counter and update last accessed."""
        self.download_count += 1
        self.last_accessed = timezone.now()
        self.save(update_fields=['download_count', 'last_accessed'])


class AnonymizationTask(models.Model):
    """
    Model to track anonymization tasks and their status for frontend polling.
    """
    
    STATUS_CHOICES = [
        ('not_started', 'Not Started'),
        ('processing_anonymization', 'Processing Anonymization'),
        ('extracting_frames', 'Extracting Frames'),
        ('predicting_segments', 'Predicting Segments'),
        ('done', 'Done'),
        ('failed', 'Failed'),
        ('validated', 'Validated'),
    ]
    
    id = models.AutoField(primary_key=True)
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    
    # Link to video file
    video_file = models.ForeignKey(
        'VideoFile',
        on_delete=models.CASCADE,
        related_name='anonymization_tasks'
    )
    
    # Task status and progress
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='not_started')
    progress = models.IntegerField(default=0)  # 0-100
    message = models.TextField(blank=True, default='')
    
    # Error handling
    error_message = models.TextField(blank=True, default='')
    retry_count = models.IntegerField(default=0)
    max_retries = models.IntegerField(default=3)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    # Processing metadata
    processing_details = models.JSONField(default=dict, blank=True)
    
    class Meta:
        db_table = 'anonymization_task'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['video_file', 'status']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"AnonymizationTask {self.uuid} - {self.video_file.uuid} ({self.status})"
    
    def start_processing(self):
        """Mark task as started."""
        self.status = 'processing_anonymization'
        self.started_at = timezone.now()
        self.save(update_fields=['status', 'started_at'])
    
    def set_progress(self, progress: int, message: str = ''):
        """Update task progress."""
        self.progress = max(0, min(100, progress))
        if message:
            self.message = message
        self.save(update_fields=['progress', 'message'])
    
    def mark_completed(self, message: str = 'Processing completed successfully'):
        """Mark task as completed successfully."""
        self.status = 'done'
        self.progress = 100
        self.message = message
        self.completed_at = timezone.now()
        self.save(update_fields=['status', 'progress', 'message', 'completed_at'])
    
    def mark_failed(self, error_message: str = ''):
        """Mark task as failed."""
        self.status = 'failed'
        self.error_message = error_message
        self.completed_at = timezone.now()
        self.retry_count += 1
        self.save(update_fields=['status', 'error_message', 'completed_at', 'retry_count'])
    
    def can_retry(self) -> bool:
        """Check if task can be retried."""
        return self.retry_count < self.max_retries and self.status == 'failed'
    
    def reset_for_retry(self):
        """Reset task for retry."""
        if self.can_retry():
            self.status = 'not_started'
            self.progress = 0
            self.message = ''
            self.error_message = ''
            self.started_at = None
            self.completed_at = None
            self.save(update_fields=[
                'status', 'progress', 'message', 'error_message',
                'started_at', 'completed_at'
            ])
    
    @property
    def duration(self):
        """Get task duration if completed."""
        if self.started_at and self.completed_at:
            return self.completed_at - self.started_at
        return None
    
    @property
    def is_completed(self) -> bool:
        """Check if task is in a completed state."""
        return self.status in ['done', 'failed', 'validated']
    
    @property
    def is_processing(self) -> bool:
        """Check if task is currently processing."""
        return self.status in ['processing_anonymization', 'extracting_frames', 'predicting_segments']
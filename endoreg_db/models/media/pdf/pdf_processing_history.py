import logging

from django.conf import settings
from django.db import models

from .raw_pdf import RawPdfFile

logger = logging.getLogger(__name__)


class PdfProcessingHistory(models.Model):
    """
    Audit trail for in-place PDF correction operations.
    """

    OPERATION_PDF_REDACTION = "pdf_redaction"
    OPERATION_CHOICES = [
        (OPERATION_PDF_REDACTION, "PDF Redaction"),
    ]

    SOURCE_TYPE_RAW = "raw"
    SOURCE_TYPE_PROCESSED = "processed"
    SOURCE_TYPE_CHOICES = [
        (SOURCE_TYPE_RAW, "Raw"),
        (SOURCE_TYPE_PROCESSED, "Processed"),
    ]

    pdf = models.ForeignKey(
        RawPdfFile,
        on_delete=models.CASCADE,
        related_name="pdf_processing_history",
    )
    operation = models.CharField(
        max_length=64,
        choices=OPERATION_CHOICES,
        default=OPERATION_PDF_REDACTION,
    )
    source_type = models.CharField(
        max_length=16,
        choices=SOURCE_TYPE_CHOICES,
    )
    redaction_manifest = models.JSONField(default=dict)
    note = models.TextField(blank=True)
    client_source_sha256 = models.CharField(max_length=64, blank=True)
    source_sha256 = models.CharField(max_length=64, blank=True)
    processed_file_name = models.CharField(max_length=500, blank=True)

    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pdf_processing_history_entries",
    )
    actor_username = models.CharField(max_length=150, blank=True)
    actor_email = models.EmailField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "pdf_processing_history"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["pdf", "-created_at"]),
            models.Index(fields=["operation"]),
        ]

    def __str__(self) -> str:
        return (
            f"{self.operation} revision={self.pk} "
            f"pdf_id={self.pdf_id} user={self.actor_username or 'unknown'}"
        )

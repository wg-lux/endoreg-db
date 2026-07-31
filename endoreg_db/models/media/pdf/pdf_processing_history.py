from __future__ import annotations

import logging
from types import NoneType
from typing import TYPE_CHECKING, Any, TypeAlias

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from lx_dtypes.models.contracts.pdf_redaction import PdfRedactionManifest
from pydantic import ValidationError as PydanticValidationError

from .raw_pdf import RawPdfFile

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

logger = logging.getLogger(__name__)

NoPdfHistoryActorValue: TypeAlias = NoneType
PdfHistoryActor: TypeAlias = "AbstractBaseUser | NoPdfHistoryActorValue"


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

    pdf: models.ForeignKey[Any] = models.ForeignKey(
        RawPdfFile,
        on_delete=models.CASCADE,
        related_name="pdf_processing_history",
    )
    operation: models.CharField[Any, Any] = models.CharField(
        max_length=64,
        choices=OPERATION_CHOICES,
        default=OPERATION_PDF_REDACTION,
    )
    source_type: models.CharField[Any, Any] = models.CharField(
        max_length=16,
        choices=SOURCE_TYPE_CHOICES,
    )
    redaction_manifest: models.JSONField[dict[str, object], Any] = models.JSONField(
        default=dict
    )
    note: models.TextField[Any, Any] = models.TextField(blank=True)
    client_source_sha256: models.CharField[Any, Any] = models.CharField(
        max_length=64, blank=True
    )
    source_sha256: models.CharField[Any, Any] = models.CharField(
        max_length=64, blank=True
    )
    processed_file_name: models.CharField[Any, Any] = models.CharField(
        max_length=500, blank=True
    )

    actor_user: models.ForeignKey[Any] = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pdf_processing_history_entries",
    )
    actor_username: models.CharField[Any, Any] = models.CharField(
        max_length=150, blank=True
    )
    actor_email: models.EmailField[Any, Any] = models.EmailField(blank=True)

    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)

    if TYPE_CHECKING:
        pdf_id: int

    class Meta:
        db_table = "pdf_processing_history"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["pdf", "-created_at"]),
            models.Index(fields=["operation"]),
        ]

    def clean(self) -> None:
        super().clean()
        try:
            manifest = PdfRedactionManifest.model_validate(self.redaction_manifest)
        except PydanticValidationError as exc:
            raise ValidationError({"redaction_manifest": str(exc)}) from exc
        self.redaction_manifest = manifest.model_dump(mode="json")

    def save(self, *args: object, **kwargs: object) -> None:
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return (
            f"{self.operation} revision={self.pk} "
            f"pdf_id={self.pdf_id} user={self.actor_username or 'unknown'}"
        )

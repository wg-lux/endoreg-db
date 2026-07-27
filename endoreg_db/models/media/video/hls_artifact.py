from __future__ import annotations

import uuid as uuid_lib
from datetime import datetime
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import models
from endoreg_db.utils.validation_types import ValidationErrorMessageArg

if TYPE_CHECKING:
    pass


class VideoHlsArtifact(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        MATERIALIZING = "materializing", "Materializing"
        VALIDATED = "validated", "Validated"
        READY = "ready", "Ready"
        SUPERSEDED = "superseded", "Superseded"
        FAILED = "failed", "Failed"

    class ArtifactKind(models.TextChoices):
        RAW = "raw", "Raw"
        PROCESSED = "processed", "Processed"

    class ErrorCode(models.TextChoices):
        NONE = "", "None"
        DISPATCH_FAILED = "dispatch_failed", "Dispatch Failed"
        INCONSISTENT_ARTIFACT = "inconsistent_artifact", "Inconsistent Artifact"
        MATERIALIZATION_FAILED = "materialization_failed", "Materialization Failed"
        STALE_ATTEMPT = "stale_attempt", "Stale Attempt"

    video: models.ForeignKey[Any] = models.ForeignKey(
        "VideoFile",
        on_delete=models.CASCADE,
        related_name="hls_artifacts",
    )
    video_id: int
    artifact_kind: models.CharField[str, Any] = models.CharField(
        max_length=16,
        choices=ArtifactKind.choices,
    )
    status: models.CharField[str, Any] = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.MATERIALIZING,
    )
    key_id: models.UUIDField[uuid_lib.UUID, Any] = models.UUIDField(
        default=uuid_lib.uuid4,
        unique=True,
        editable=False,
    )
    source_generation_id: models.UUIDField[uuid_lib.UUID, Any] = models.UUIDField(
        default=uuid_lib.uuid4,
        editable=False,
        help_text="Opaque generation identifier for the source snapshot of this HLS attempt.",
    )
    key_ciphertext: models.BinaryField[bytes | None, Any] = models.BinaryField(
        null=True, blank=True
    )
    key_nonce: models.BinaryField[bytes | None, Any] = models.BinaryField(
        null=True,
        blank=True,
    )
    key_wrap_algorithm: models.CharField[str, Any] = models.CharField(
        max_length=64,
        default="AESGCM-master-wrap-v1",
    )
    iv_hex: models.CharField[str, Any] = models.CharField(max_length=32, blank=True)
    playlist_relative_path: models.CharField[str, Any] = models.CharField(
        max_length=500,
        blank=True,
    )
    segment_directory_relative_path: models.CharField[str, Any] = models.CharField(
        max_length=500,
        blank=True,
    )
    segment_count: models.PositiveIntegerField[int, Any] = models.PositiveIntegerField(
        default=0
    )
    source_file_name: models.CharField[str, Any] = models.CharField(
        max_length=500,
        blank=True,
    )
    last_error: models.TextField[str, Any] = models.TextField(blank=True)
    error_code: models.CharField[str, Any] = models.CharField(
        max_length=64,
        choices=ErrorCode.choices,
        default=ErrorCode.NONE,
        blank=True,
    )
    created_at: models.DateTimeField[datetime, Any] = models.DateTimeField(
        auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime, Any] = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["video", "artifact_kind"],
                condition=models.Q(status="ready"),
                name="unique_ready_video_hls_artifact_kind",
            ),
            models.UniqueConstraint(
                fields=["video", "artifact_kind"],
                condition=models.Q(status__in=["queued", "materializing", "validated"]),
                name="unique_active_video_hls_attempt",
            ),
            models.CheckConstraint(
                condition=(models.Q(status="failed") & ~models.Q(error_code=""))
                | (~models.Q(status="failed") & models.Q(error_code="")),
                name="video_hls_failure_coded",
            ),
        ]
        indexes = [
            models.Index(fields=["video", "artifact_kind", "status"]),
            models.Index(fields=["key_id", "status"]),
        ]

    def clean(self) -> None:
        errors: dict[str, ValidationErrorMessageArg] = {}
        if self.key_nonce is not None and len(self.key_nonce) != 12:
            errors["key_nonce"] = "HLS key nonce must be 12 bytes."
        if self.iv_hex:
            if len(self.iv_hex) != 32:
                errors["iv_hex"] = "HLS IV must be 32 hexadecimal characters."
            elif any(char not in "0123456789abcdefABCDEF" for char in self.iv_hex):
                errors["iv_hex"] = "HLS IV must be hexadecimal."
        if self.status == self.Status.FAILED.value and not self.error_code:
            errors["error_code"] = "Failed HLS artifacts require an error code."
        if self.status != self.Status.FAILED.value and self.error_code:
            errors["error_code"] = "Only failed HLS artifacts may have an error code."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return (
            f"HLS artifact video={self.video_id} "
            f"kind={self.artifact_kind} status={self.status}"
        )

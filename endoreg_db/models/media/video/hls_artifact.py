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
    class ArtifactKind(models.TextChoices):
        RAW = "raw", "Raw"
        PROCESSED = "processed", "Processed"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        MATERIALIZING = "materializing", "Materializing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

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
                name="unique_video_hls_artifact_kind",
            )
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
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return (
            f"HLS artifact video={self.video_id} "
            f"kind={self.artifact_kind} status={self.status}"
        )

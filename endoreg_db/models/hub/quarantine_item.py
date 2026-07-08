from __future__ import annotations

import uuid
from datetime import datetime
from types import NoneType
from typing import Any, TYPE_CHECKING, TypeAlias

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

from endoreg_db.schemas import validate_quarantine_item_metadata
from endoreg_db.utils.validation_types import ValidationErrorMessageArg

if TYPE_CHECKING:
    from .upload_job import UploadJob

NoQuarantineRelationValue: TypeAlias = NoneType
QuarantineUploadJob: TypeAlias = "UploadJob | NoQuarantineRelationValue"
QuarantineUser: TypeAlias = "User | NoQuarantineRelationValue"
QuarantineDateTime: TypeAlias = "datetime | NoQuarantineRelationValue"


class QuarantineItem(models.Model):
    class Status(models.TextChoices):
        PENDING_REVIEW = "pending_review", "Pending Review"
        RETAINED = "retained", "Retained"
        APPROVED_FOR_DELETION = "approved_for_deletion", "Approved For Deletion"
        DELETED = "deleted", "Deleted"
        MISSING = "missing", "Missing"
        FAILED = "failed", "Failed"

    id: models.UUIDField[uuid.UUID, Any] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    path: models.TextField[str, Any] = models.TextField(unique=True)
    relative_path: models.TextField[str, Any] = models.TextField(db_index=True)
    original_filename: models.CharField[str, Any] = models.CharField(
        max_length=512,
        blank=True,
    )
    size_bytes: models.BigIntegerField[int, Any] = models.BigIntegerField(default=0)
    file_mtime_ns: models.BigIntegerField[int, Any] = models.BigIntegerField(default=0)
    quarantined_at: models.DateTimeField[datetime, Any] = models.DateTimeField()
    last_seen_at: models.DateTimeField[datetime, Any] = models.DateTimeField()
    status: models.CharField[str, Any] = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING_REVIEW,
        db_index=True,
    )
    source_upload_job: models.ForeignKey[Any] = models.ForeignKey(
        "UploadJob",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="quarantine_items",
    )
    metadata: models.JSONField[Any, Any] = models.JSONField(
        default=dict,
        blank=True,
    )
    decision_reason: models.TextField[str, Any] = models.TextField(blank=True)
    reviewed_by: models.ForeignKey[Any] = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_quarantine_items",
    )
    reviewed_at: models.DateTimeField[QuarantineDateTime, Any] = models.DateTimeField(
        null=True, blank=True
    )
    delete_eligible_at: models.DateTimeField[QuarantineDateTime, Any] = (
        models.DateTimeField(null=True, blank=True)
    )
    deleted_at: models.DateTimeField[QuarantineDateTime, Any] = models.DateTimeField(
        null=True, blank=True
    )
    error_detail: models.TextField[str, Any] = models.TextField(blank=True)
    created_at: models.DateTimeField[datetime, Any] = models.DateTimeField(
        auto_now_add=True,
    )
    updated_at: models.DateTimeField[datetime, Any] = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["status", "-quarantined_at", "relative_path"]
        indexes = [
            models.Index(
                fields=["status", "delete_eligible_at"],
                name="quarantine_status_delete_idx",
            ),
            models.Index(
                fields=["last_seen_at"],
                name="quarantine_last_seen_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.relative_path} ({self.status})"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationErrorMessageArg] = {}
        if not self.path.strip():
            errors["path"] = "path is required"
        if not self.relative_path.strip():
            errors["relative_path"] = "relative_path is required"
        if self.relative_path.startswith("/"):
            errors["relative_path"] = "relative_path must not be absolute"
        if self.size_bytes < 0:
            errors["size_bytes"] = "size_bytes must not be negative"
        if self.file_mtime_ns < 0:
            errors["file_mtime_ns"] = "file_mtime_ns must not be negative"
        if self.status == self.Status.DELETED.value and self.deleted_at is None:
            errors["deleted_at"] = "deleted_at is required for deleted items"
        try:
            self.metadata = validate_quarantine_item_metadata(self.metadata)
        except ValueError as exc:
            errors["metadata"] = str(exc)
        if errors:
            raise ValidationError(errors)

    def save(self, *args: object, **kwargs: object) -> None:
        self.clean()
        super().save(*args, **kwargs)

"""Persistent file inventory, independent of mutable upload provenance payloads."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from .upload_job import UploadJob


class UploadJobFile(models.Model):
    class Role(models.TextChoices):
        SOURCE = "source", "Import source"
        SIDECAR = "sidecar", "Import sidecar"
        WORKING = "working", "Disposable working file"
        RETAINED = "retained", "Retained media artifact"
        QUARANTINE = "quarantine", "Quarantined file"

    upload_job: models.ForeignKey["UploadJob", "UploadJob"] = models.ForeignKey(
        "UploadJob", on_delete=models.CASCADE, related_name="files"
    )
    path: models.CharField[str, str] = models.CharField(max_length=2048, db_index=True)
    role: models.CharField[str, str] = models.CharField(
        max_length=16, choices=Role.choices
    )
    is_directory: models.BooleanField[bool, bool] = models.BooleanField(default=False)
    size_bytes: models.PositiveBigIntegerField[int | None, int | None] = (
        models.PositiveBigIntegerField(null=True)
    )
    removed_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True)
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now=True
    )

    objects = models.Manager["UploadJobFile"]()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["upload_job", "path"], name="unique_upload_job_file_path"
            )
        ]

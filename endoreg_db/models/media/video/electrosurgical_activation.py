"""Immutable original activation export, encrypted and bound to one recording."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Unpack

from django.core.exceptions import ValidationError
from django.db import models
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from endoreg_db.schemas.electrosurgical_activation import ActivationImportMetadata
from endoreg_db.utils.encryption.encrypted import LazyEncryptedStorage
from endoreg_db.utils.paths import get_runtime_paths

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def activation_export_upload_to(
    instance: VideoElectrosurgicalActivationLog, filename: str
) -> str:
    del filename
    metadata = ActivationImportMetadata.model_validate(instance.metadata)
    return (
        f"{get_runtime_paths().documents.name}/electrosurgical_activations/"
        f"{instance.video_id}/{instance.source_sha256}.{metadata.source_format.value}"
    )


class VideoElectrosurgicalActivationLog(models.Model):
    objects = models.Manager["VideoElectrosurgicalActivationLog"]()
    video_id: int
    video: models.ForeignKey[VideoFile] = models.ForeignKey(
        "endoreg_db.VideoFile",
        on_delete=models.PROTECT,
        related_name="electrosurgical_activation_logs",
    )
    source_sha256: models.CharField[str, str] = models.CharField(max_length=64)
    source_file: models.FileField = models.FileField(
        upload_to=activation_export_upload_to,
        storage=LazyEncryptedStorage(),
        max_length=255,
    )
    metadata: models.JSONField[JsonObject, JsonObject] = models.JSONField()
    imported_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        ordering = ["video_id", "imported_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["video", "source_sha256"], name="unique_video_activation_source"
            )
        ]

    def clean(self) -> None:
        super().clean()
        try:
            metadata = ActivationImportMetadata.model_validate(self.metadata)
            if metadata.source_sha256 != self.source_sha256:
                raise ValueError("Activation source identity is inconsistent")
            if (
                metadata.synchronization.source_content_hash
                != self.video.raw_video_hash
            ):
                raise ValueError("Activation source is bound to another recording")
            self.metadata = metadata.model_dump(mode="json")
        except ValueError as exc:
            raise ValidationError({"metadata": str(exc)}) from exc

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        if not self._state.adding:
            raise ValueError("Activation source rows are immutable")
        self.clean()
        super().save(*args, **kwargs)

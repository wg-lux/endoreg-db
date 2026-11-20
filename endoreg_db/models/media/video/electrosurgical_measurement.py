"""Imported high-frequency (HF) electrosurgical measurement series of one video.

Persistence only. The verbatim lx_dtypes ``ElectrosurgicalMeasurementSeries``
JSON document is stored as ciphertext through the encrypted storage backend;
SQL keeps its identities and non-sample metadata. Import, synchronization
checks and frame alignment belong to ``endoreg_db.services.electrical``.
Rows are immutable: a changed measurement or profile is a new import.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Unpack

from django.core.exceptions import ValidationError
from django.db import models
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from endoreg_db.schemas.electrosurgical_measurement import (
    dump_aggregation_profile,
    dump_channel_descriptors,
)
from endoreg_db.utils.encryption.encrypted import LazyEncryptedStorage
from endoreg_db.utils.paths import get_runtime_paths

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_ID = re.compile(r"^[0-9a-f]{32}$")


def electrosurgical_measurement_upload_to(
    instance: VideoElectrosurgicalMeasurement, filename: str
) -> str:
    """Content-addressed name below the protected documents storage area."""
    del filename
    documents = get_runtime_paths().documents.name
    return f"{documents}/electrosurgical_measurements/{instance.payload_sha256}.json"


class VideoElectrosurgicalMeasurement(models.Model):
    """One imported measurement series bound to one video's recording identity."""

    objects = models.Manager["VideoElectrosurgicalMeasurement"]()

    video_id: int

    video: models.ForeignKey[VideoFile] = models.ForeignKey(
        "endoreg_db.VideoFile",
        # Deleting a video must handle its encrypted measurement source explicitly.
        on_delete=models.PROTECT,
        related_name="electrosurgical_measurements",
    )
    source_file: models.FileField = models.FileField(
        upload_to=electrosurgical_measurement_upload_to,
        storage=LazyEncryptedStorage(),
        max_length=255,
    )
    # SHA-256 of the stored plaintext JSON document; verified on every read.
    payload_sha256: models.CharField[str, str] = models.CharField(max_length=64)
    # ``ElectrosurgicalMeasurementSeries.source_sha256``: the device export file.
    source_sha256: models.CharField[str, str] = models.CharField(
        max_length=64, db_index=True
    )
    # ``VideoFile.raw_video_hash`` when the series was bound to the recording.
    recording_content_hash: models.CharField[str, str] = models.CharField(max_length=64)
    schema_version: models.CharField[str, str] = models.CharField(max_length=8)
    parser_id: models.CharField[str, str] = models.CharField(max_length=64)
    parser_version: models.CharField[str, str] = models.CharField(max_length=64)
    session_id: models.CharField[str, str] = models.CharField(max_length=32)
    electrical_clock_id: models.CharField[str, str] = models.CharField(max_length=32)
    time_base_num: models.BigIntegerField[int, int] = models.BigIntegerField()
    time_base_den: models.BigIntegerField[int, int] = models.BigIntegerField()
    sample_count: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    first_sample_timestamp: models.BigIntegerField[int, int] = models.BigIntegerField()
    last_sample_timestamp: models.BigIntegerField[int, int] = models.BigIntegerField()
    channels: models.JSONField[list[JsonObject], list[JsonObject]] = models.JSONField()
    aggregation_profile: models.JSONField[JsonObject, JsonObject] = models.JSONField()
    imported_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        ordering = ["video_id", "imported_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["video", "source_sha256"],
                name="unique_video_electrosurgical_source",
            ),
            models.CheckConstraint(
                condition=models.Q(time_base_num__gt=0, time_base_den__gt=0),
                name="electrosurgical_positive_time_base",
            ),
            models.CheckConstraint(
                condition=models.Q(sample_count__gt=0),
                name="electrosurgical_nonempty_series",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    last_sample_timestamp__gte=models.F("first_sample_timestamp")
                ),
                name="electrosurgical_ordered_sample_bounds",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        for field in ("payload_sha256", "source_sha256", "recording_content_hash"):
            if not _SHA256.fullmatch(str(getattr(self, field))):
                errors[field] = "Expected a lowercase SHA-256 hex digest"
        for field in ("session_id", "electrical_clock_id"):
            if not _OPAQUE_ID.fullmatch(str(getattr(self, field))):
                errors[field] = "Expected an opaque 32-character hex identifier"
        try:
            self.channels = dump_channel_descriptors(self.channels)
        except ValueError as exc:
            errors["channels"] = str(exc)
        try:
            self.aggregation_profile = dump_aggregation_profile(
                self.aggregation_profile
            )
        except ValueError as exc:
            errors["aggregation_profile"] = str(exc)
        if errors:
            raise ValidationError(errors)

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        if not self._state.adding:
            raise ValueError("Electrosurgical measurement rows are immutable")
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"VideoElectrosurgicalMeasurement(id={self.pk}, video={self.video_id})"

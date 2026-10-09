"""Immutable out-of-band frame lineage, independent of media publication state."""

from __future__ import annotations

from typing import Unpack
from datetime import datetime

from django.db import models
from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.contracts.video_provenance import VideoFrameProvenancePayload

from endoreg_db.helpers.typing import DjangoModelSaveKwargs


class VideoFrameProvenance(models.Model):
    source_content_hash: models.CharField[str, str] = models.CharField(
        max_length=64, db_index=True
    )
    output_content_hash: models.CharField[str, str] = models.CharField(
        max_length=64, db_index=True
    )
    payload: models.JSONField[JsonObject, JsonObject] = models.JSONField()
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source_content_hash", "output_content_hash"],
                name="unique_video_frame_provenance_edge",
            )
        ]

    def clean(self) -> None:
        super().clean()
        proof = VideoFrameProvenancePayload.model_validate(self.payload)
        if (proof.source.content_hash, proof.output.content_hash) != (
            self.source_content_hash,
            self.output_content_hash,
        ):
            raise ValueError("Frame provenance content identities do not match")
        self.payload = proof.model_dump(mode="json")

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        if not self._state.adding:
            raise ValueError("Frame provenance rows are immutable")
        self.clean()
        super().save(*args, **kwargs)

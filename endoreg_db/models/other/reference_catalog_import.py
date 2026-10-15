"""Immutable provenance for projected reference catalogues; no workflow logic."""

from __future__ import annotations

from datetime import datetime
from typing import Unpack

from django.core.exceptions import ValidationError
from django.db import models
from lx_dtypes.models.contracts.json_types import JsonObject
from pydantic import ValidationError as SchemaValidationError

from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from lx_dtypes.models.contracts.reference_catalog_snapshot import (
    ReferenceCatalogReceipt,
)


class ReferenceCatalogImport(models.Model):
    objects = models.Manager["ReferenceCatalogImport"]()

    module: models.CharField[str, str] = models.CharField(max_length=255)
    version: models.CharField[str, str] = models.CharField(max_length=100)
    projection: models.CharField[str, str] = models.CharField(
        max_length=2048, default="all"
    )
    snapshot_id: models.CharField[str, str] = models.CharField(max_length=71)
    payload: models.JSONField[JsonObject, JsonObject] = models.JSONField()
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["module", "version", "projection"],
                name="unique_reference_catalog_import",
            )
        ]

    def clean(self) -> None:
        super().clean()
        try:
            receipt = ReferenceCatalogReceipt.model_validate(self.payload)
        except SchemaValidationError as exc:
            raise ValidationError("Invalid reference catalogue receipt") from exc
        identity = receipt.snapshot.identity
        if (
            identity.knowledge_base_module != self.module
            or identity.knowledge_base_version != self.version
            or receipt.snapshot.snapshot_id != self.snapshot_id
            or receipt.snapshot.projection != self.projection
        ):
            raise ValidationError("Reference catalogue receipt identity mismatch")
        if self.pk is not None:
            previous = type(self).objects.get(pk=self.pk)
            if (
                previous.module != self.module
                or previous.version != self.version
                or previous.projection != self.projection
                or previous.snapshot_id != self.snapshot_id
                or previous.payload != self.payload
            ):
                raise ValidationError("Reference catalogue receipts are immutable")

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.clean()
        super().save(*args, **kwargs)

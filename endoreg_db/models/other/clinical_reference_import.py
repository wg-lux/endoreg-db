"""Immutable provenance for projected clinical references; no workflow logic."""

from __future__ import annotations

from datetime import datetime
from typing import Unpack

from django.core.exceptions import ValidationError
from django.db import models
from lx_dtypes.models.contracts.json_types import JsonObject
from pydantic import ValidationError as SchemaValidationError

from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from endoreg_db.schemas.clinical_reference import ClinicalReferenceReceipt


class ClinicalReferenceImport(models.Model):
    objects = models.Manager["ClinicalReferenceImport"]()

    module: models.CharField[str, str] = models.CharField(max_length=255)
    version: models.CharField[str, str] = models.CharField(max_length=100)
    snapshot_id: models.CharField[str, str] = models.CharField(max_length=71)
    payload: models.JSONField[JsonObject, JsonObject] = models.JSONField()
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["module", "version"], name="unique_clinical_reference_import"
            )
        ]

    def clean(self) -> None:
        super().clean()
        try:
            receipt = ClinicalReferenceReceipt.model_validate(self.payload)
        except SchemaValidationError as exc:
            raise ValidationError("Invalid clinical reference receipt") from exc
        identity = receipt.snapshot.identity
        if (
            identity.knowledge_base_module != self.module
            or identity.knowledge_base_version != self.version
            or receipt.snapshot.snapshot_id != self.snapshot_id
        ):
            raise ValidationError("Clinical reference receipt identity mismatch")
        if self.pk is not None:
            previous = type(self).objects.get(pk=self.pk)
            if (
                previous.module != self.module
                or previous.version != self.version
                or previous.snapshot_id != self.snapshot_id
                or previous.payload != self.payload
            ):
                raise ValidationError("Clinical reference receipts are immutable")

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.clean()
        super().save(*args, **kwargs)

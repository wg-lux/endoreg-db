"""Immutable provenance of authorized study-definition provisioning."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Unpack, cast

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.contracts.study_setup import StudySetupImportReceipt
from pydantic import ValidationError as SchemaError

from endoreg_db.helpers.typing import DjangoModelSaveKwargs

if TYPE_CHECKING:
    from django.contrib.auth.models import User


class StudySetupImport(models.Model):
    objects = models.Manager["StudySetupImport"]()
    owner: models.ForeignKey[User] = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT
    )
    definition_id: models.CharField[str, str] = models.CharField(max_length=128)
    definition_version: models.CharField[str, str] = models.CharField(max_length=64)
    content_sha256: models.CharField[str, str] = models.CharField(max_length=64)
    payload: models.JSONField[JsonObject, JsonObject] = models.JSONField()
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "definition_id", "definition_version"],
                name="unique_owned_study_setup_version",
            )
        ]

    def validated_receipt(self) -> StudySetupImportReceipt:
        try:
            receipt = StudySetupImportReceipt.model_validate(self.payload)
        except SchemaError as error:
            raise ValidationError("Invalid study setup receipt") from error
        if (
            receipt.definition.definition_id,
            receipt.definition.definition_version,
        ) != (self.definition_id, self.definition_version):
            raise ValidationError("Receipt definition identity mismatch")
        if receipt.definition.content_sha256 != self.content_sha256:
            raise ValidationError("Receipt content digest mismatch")
        return receipt

    def clean(self) -> None:
        super().clean()
        receipt = self.validated_receipt()
        self.payload = cast(JsonObject, receipt.model_dump(mode="json"))
        if self.pk is not None:
            raise ValidationError("Study setup receipts are immutable")

    def save(self, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.full_clean()
        super().save(**kwargs)

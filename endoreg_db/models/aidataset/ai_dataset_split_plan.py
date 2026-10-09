from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Unpack

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.helpers.typing import DjangoModelSaveKwargs

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from endoreg_db.models.aidataset.aidataset import AIDataSet


class AIDatasetSplitPlan(models.Model):
    """Immutable, validated annotation membership snapshot."""

    objects = models.Manager["AIDatasetSplitPlan"]()

    dataset: models.ForeignKey[AIDataSet | None] = models.ForeignKey(
        "endoreg_db.AIDataSet", on_delete=models.SET_NULL, null=True
    )
    created_by: models.ForeignKey[User] = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )
    snapshot: models.JSONField[JsonObject, JsonObject] = models.JSONField()

    def clean(self) -> None:
        from pydantic import ValidationError as SchemaError

        from endoreg_db.schemas.ai_dataset_splits import SplitSnapshot

        super().clean()
        try:
            self.snapshot = SplitSnapshot.model_validate(self.snapshot).model_dump(
                mode="json"
            )
        except SchemaError as error:
            raise ValidationError(
                {"snapshot": "Invalid split membership snapshot."}
            ) from error

    def save(self, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        if not self._state.adding:
            raise ValidationError("Split plans are immutable; create a new plan.")
        self.full_clean()
        super().save(**kwargs)

    class Meta:
        ordering = ["-created_at", "-pk"]

"""Persistent study identity; cohort selection and workflows belong to services."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Unpack
from uuid import UUID, uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from lx_dtypes.models.contracts.study import StudyDefinition
from pydantic import ValidationError as SchemaError

from endoreg_db.helpers.typing import DjangoModelSaveKwargs

if TYPE_CHECKING:
    from django.contrib.auth.models import User


class Study(models.Model):
    objects = models.Manager["Study"]()

    if TYPE_CHECKING:
        created_by_id: int

    uuid: models.UUIDField[UUID, UUID] = models.UUIDField(
        default=uuid4, unique=True, editable=False
    )
    name: models.CharField[str, str] = models.CharField(max_length=255)
    hypothesis: models.TextField[str, str] = models.TextField(blank=True, default="")
    description: models.TextField[str, str] = models.TextField(blank=True, default="")
    created_by: models.ForeignKey[User] = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="studies"
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        ordering = ["name", "pk"]

    def clean(self) -> None:
        super().clean()
        try:
            definition = StudyDefinition.model_validate(
                {
                    "name": self.name,
                    "hypothesis": self.hypothesis,
                    "description": self.description,
                }
            )
        except SchemaError as error:
            raise ValidationError("Invalid study definition") from error
        self.name = definition.name
        self.hypothesis = definition.hypothesis
        self.description = definition.description

    def save(self, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.full_clean()
        super().save(**kwargs)

    def __str__(self) -> str:
        return self.name

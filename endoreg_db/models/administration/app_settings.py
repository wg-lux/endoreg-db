from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from types import NoneType
from typing import TYPE_CHECKING, TypeAlias, cast

from django.db import models
from django.db.models.base import ModelBase

if TYPE_CHECKING:
    from ..aidataset.aidataset import AIDataSet
    from ..medical.hardware.endoscopy_processor import EndoscopyProcessor
    from .center.center import Center

NoApplicationSettingsSaveValue: TypeAlias = NoneType
ApplicationSettingsForceInsert: TypeAlias = bool | tuple[ModelBase, ...]
ApplicationSettingsUsing: TypeAlias = str | NoApplicationSettingsSaveValue
ApplicationSettingsUpdateFields: TypeAlias = (
    Iterable[str] | NoApplicationSettingsSaveValue
)
ApplicationSettingsSavePositional: TypeAlias = (
    ApplicationSettingsForceInsert
    | bool
    | ApplicationSettingsUsing
    | ApplicationSettingsUpdateFields
)


class ApplicationSettingsManager(models.Manager["ApplicationSettings"]):
    def get_solo(self) -> "ApplicationSettings":
        obj, _ = self.get_or_create(pk=1)
        return obj


class ApplicationSettings(models.Model):
    """
    Singleton-like persisted application defaults.

    Stores central defaults used by imports/annotation/report workflows.
    """

    center: models.ForeignKey[Center, Center] = models.ForeignKey(
        "Center",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    processor: models.ForeignKey[EndoscopyProcessor, EndoscopyProcessor] = (
        models.ForeignKey(
            "EndoscopyProcessor",
            on_delete=models.SET_NULL,
            null=True,
            blank=True,
            related_name="+",
        )
    )
    annotator_name: models.CharField[str, str] = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    report_template_name: models.CharField[str, str] = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    ai_dataset_name: models.CharField[str, str] = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    ai_dataset_type: models.CharField[str, str] = models.CharField(
        max_length=32,
        blank=True,
        default="",
        choices=[
            ("", "Unset"),
            ("image", "Image"),
            ("video", "Video"),
        ],
    )
    ai_dataset: models.ForeignKey[AIDataSet, AIDataSet] = models.ForeignKey(
        "AIDataSet",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now=True
    )

    objects = ApplicationSettingsManager()

    class Meta:
        verbose_name = "Application Settings"
        verbose_name_plural = "Application Settings"

    def save(
        self,
        *args: ApplicationSettingsSavePositional,
        force_insert: ApplicationSettingsForceInsert = False,
        force_update: bool = False,
        using: ApplicationSettingsUsing = None,
        update_fields: ApplicationSettingsUpdateFields = None,
    ) -> None:
        # Enforce singleton row semantics.
        self.pk = 1
        super().save(
            *args,
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    @classmethod
    def get_solo(cls) -> "ApplicationSettings":
        return cast(ApplicationSettingsManager, cls.objects).get_solo()

    def __str__(self) -> str:
        return "Application Settings"

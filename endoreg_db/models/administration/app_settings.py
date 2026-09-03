from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias, Unpack, cast, Any

from django.db import models

from endoreg_db.helpers.typing import DjangoModelSaveKwargs

if TYPE_CHECKING:
    from ..aidataset.aidataset import AIDataSet
    from ..medical.hardware.endoscopy_processor import EndoscopyProcessor
    from .center.center import Center

NoApplicationSettingsSaveValue: TypeAlias = None


class ApplicationSettingsManager(models.Manager["ApplicationSettings"]):
    def get_solo(self) -> "ApplicationSettings":
        obj, _ = self.get_or_create(pk=1)
        return obj


class ApplicationSettings(models.Model):
    """
    Singleton-like persisted application defaults.

    Stores central defaults used by imports/annotation/report workflows.
    """

    center: models.ForeignKey["Center | NoApplicationSettingsSaveValue"] = (
        models.ForeignKey(
            "Center",
            on_delete=models.SET_NULL,
            null=True,
            blank=True,
            related_name="+",
        )
    )
    processor: models.ForeignKey[
        "EndoscopyProcessor | NoApplicationSettingsSaveValue"
    ] = models.ForeignKey(
        "EndoscopyProcessor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    annotator_name: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    report_template_name: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    ai_dataset_name: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    ai_dataset_type: models.CharField[Any, Any] = models.CharField(
        max_length=32,
        blank=True,
        default="",
        choices=[
            ("", "Unset"),
            ("image", "Image"),
            ("video", "Video"),
        ],
    )
    ai_dataset: models.ForeignKey["AIDataSet | NoApplicationSettingsSaveValue"] = (
        models.ForeignKey(
            "AIDataSet",
            on_delete=models.SET_NULL,
            null=True,
            blank=True,
            related_name="+",
        )
    )
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

    objects = ApplicationSettingsManager()

    class Meta:
        verbose_name = "Application Settings"
        verbose_name_plural = "Application Settings"

    def save(self, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        # Enforce singleton row semantics.
        self.pk = 1
        super().save(**kwargs)

    @classmethod
    def get_solo(cls) -> "ApplicationSettings":
        return cast(ApplicationSettingsManager, cls.objects).get_solo()

    def __str__(self) -> str:
        return "Application Settings"

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    pass


class ApplicationSettingsManager(models.Manager["ApplicationSettings"]):
    def get_solo(self) -> "ApplicationSettings":
        obj, _ = self.get_or_create(pk=1)
        return obj


class ApplicationSettings(models.Model):
    """
    Singleton-like persisted application defaults.

    Stores central defaults used by imports/annotation/report workflows.
    """

    center = models.ForeignKey(
        "Center",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    processor = models.ForeignKey(
        "EndoscopyProcessor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    annotator_name = models.CharField(max_length=255, blank=True, default="")
    report_template_name = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ApplicationSettingsManager()

    class Meta:
        verbose_name = "Application Settings"
        verbose_name_plural = "Application Settings"

    def save(self, *args, **kwargs):
        # Enforce singleton row semantics.
        self.pk = 1
        return super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls) -> "ApplicationSettings":
        return cls.objects.get_solo()

    def __str__(self) -> str:
        return "Application Settings"

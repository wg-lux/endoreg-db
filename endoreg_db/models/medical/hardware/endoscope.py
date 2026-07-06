from __future__ import annotations
from typing import TYPE_CHECKING

from django.db import models


class EndoscopeManager(models.Manager["Endoscope"]):
    def get_by_natural_key(self, name: str, sn: str) -> "Endoscope":
        return self.get(name=name, sn=sn)


class Endoscope(models.Model):
    objects = EndoscopeManager()

    name: models.CharField[str] = models.CharField(max_length=255)
    sn: models.CharField[str] = models.CharField(max_length=255)
    center: models.ForeignKey["Center | None"] = models.ForeignKey(
        "Center",
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name="endoscopes",
    )
    endoscope_type: models.ForeignKey["EndoscopeType | None"] = models.ForeignKey(
        "EndoscopeType",
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name="endoscopes",
    )

    if TYPE_CHECKING:
        from endoreg_db.models import Center

    def natural_key(self) -> tuple[str, str]:
        return (str(self.name), str(self.sn))

    def __str__(self) -> str:
        return str(self.name)

    class Meta:
        ordering = ["name"]
        verbose_name = "Endoscope"
        verbose_name_plural = "Endoscopes"

    @property
    def center_safe(self) -> "Center":
        if self.center is None:
            raise ValueError("Endoscope has no associated center.")
        return self.center


class EndoscopeTypeManager(models.Manager["EndoscopeType"]):
    def get_by_natural_key(self, name: str) -> "EndoscopeType":
        return self.get(name=name)


class EndoscopeType(models.Model):
    objects = EndoscopeTypeManager()

    name: models.CharField[str] = models.CharField(max_length=255, unique=True)

    if TYPE_CHECKING:
        endoscopes: models.QuerySet["Endoscope"]

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)

    class Meta:
        ordering = ["name"]
        verbose_name = "Endoscope Type"
        verbose_name_plural = "Endoscope Types"

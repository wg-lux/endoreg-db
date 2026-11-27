from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager

if TYPE_CHECKING:
    from endoreg_db.models import ReportReaderConfig


class ReportReaderFlag(models.Model):
    objects = NameNaturalKeyManager["ReportReaderFlag"]()
    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    value: models.CharField[Any, Any] = models.CharField(max_length=255)

    if TYPE_CHECKING:

        @property
        def report_reader_configs_patient_info_line(
            self,
        ) -> models.QuerySet["ReportReaderConfig"]: ...
        @property
        def report_reader_configs_endoscope_info_line(
            self,
        ) -> models.QuerySet["ReportReaderConfig"]: ...
        @property
        def report_reader_configs_examiner_info_line(
            self,
        ) -> models.QuerySet["ReportReaderConfig"]: ...
        @property
        def report_reader_configs_cut_off_below(
            self,
        ) -> models.QuerySet["ReportReaderConfig"]: ...
        @property
        def report_reader_configs_cut_off_above(
            self,
        ) -> models.QuerySet["ReportReaderConfig"]: ...

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def __str__(self) -> str:
        return self.name

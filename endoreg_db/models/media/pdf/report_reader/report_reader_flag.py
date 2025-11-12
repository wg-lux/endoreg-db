from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import ReportReaderConfig


class ReportReaderFlagManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class ReportReaderFlag(models.Model):
    objects = ReportReaderFlagManager()
    name = models.CharField(max_length=255, unique=True)
    value = models.CharField(max_length=255)

    if TYPE_CHECKING:

        @property
        def report_reader_configs_patient_info_line(self) -> models.QuerySet["ReportReaderConfig"]: ...
        @property
        def report_reader_configs_endoscope_info_line(self) -> models.QuerySet["ReportReaderConfig"]: ...
        @property
        def report_reader_configs_examiner_info_line(self) -> models.QuerySet["ReportReaderConfig"]: ...
        @property
        def report_reader_configs_cut_off_below(self) -> models.QuerySet["ReportReaderConfig"]: ...
        @property
        def report_reader_configs_cut_off_above(self) -> models.QuerySet["ReportReaderConfig"]: ...

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name

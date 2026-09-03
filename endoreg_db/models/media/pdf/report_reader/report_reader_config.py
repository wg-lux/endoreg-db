# ReportReaderConfig Class
from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models.administration.center.center import Center
    from endoreg_db.models.administration.person.names.first_name import FirstName
    from endoreg_db.models.administration.person.names.last_name import LastName
    from .report_reader_flag import ReportReaderFlag


_SetModel = TypeVar("_SetModel", bound=models.Model, contravariant=True)
_RowModel = TypeVar("_RowModel", bound=models.Model, covariant=True)


class _ManyToManySetter(Protocol[_SetModel]):
    def set(
        self,
        objs: models.QuerySet[_SetModel] | Iterable[_SetModel | int],
        *,
        clear: bool = False,
    ) -> None: ...


class _ManyToManyRows(Protocol[_RowModel]):
    def all(self) -> models.QuerySet[_RowModel]: ...


class _ReportReaderPdfType(Protocol):
    patient_info_line: "ReportReaderFlag"
    endoscope_info_line: "ReportReaderFlag"
    examiner_info_line: "ReportReaderFlag"
    cut_off_below_lines: _ManyToManyRows["ReportReaderFlag"]
    cut_off_above_lines: _ManyToManyRows["ReportReaderFlag"]


class ReportReaderConfig(models.Model):
    """
    Configuration settings for parsing report reports (ReportReader).

    Stores locale, name lists, date format, and flags used to identify key information lines
    and text sections to ignore.
    """

    locale: models.CharField[str, Any] = models.CharField(
        default="de_DE", max_length=10
    )
    first_names: models.ManyToManyField[FirstName, FirstName] = models.ManyToManyField(
        "FirstName", related_name="report_reader_configs"
    )
    last_names: models.ManyToManyField[LastName, LastName] = models.ManyToManyField(
        "LastName", related_name="report_reader_configs"
    )
    text_date_format: models.CharField[str, Any] = models.CharField(
        default="%d.%m.%Y", max_length=10
    )
    patient_info_line_flag: models.ForeignKey[Any] = models.ForeignKey(
        "ReportReaderFlag",
        related_name="report_reader_configs_patient_info_line",
        on_delete=models.CASCADE,
    )
    endoscope_info_line_flag: models.ForeignKey[Any] = models.ForeignKey(
        "ReportReaderFlag",
        related_name="report_reader_configs_endoscope_info_line",
        on_delete=models.CASCADE,
    )
    examiner_info_line_flag: models.ForeignKey[Any] = models.ForeignKey(
        "ReportReaderFlag",
        related_name="report_reader_configs_examiner_info_line",
        on_delete=models.CASCADE,
    )
    cut_off_below: models.ManyToManyField[ReportReaderFlag, ReportReaderFlag] = (
        models.ManyToManyField(
            "ReportReaderFlag", related_name="report_reader_configs_cut_off_below"
        )
    )
    cut_off_above: models.ManyToManyField[ReportReaderFlag, ReportReaderFlag] = (
        models.ManyToManyField(
            "ReportReaderFlag", related_name="report_reader_configs_cut_off_above"
        )
    )

    if TYPE_CHECKING:
        pk: int

    def __str__(self) -> str:
        """Returns a string representation including the locale and primary key."""
        _str = f"ReportReaderConfig: {self.locale} (id: {self.pk}\n"
        return _str

    def update_names_by_center(self, center: "Center", save: bool = True) -> None:
        """Updates the first and last name lists based on the names associated with a Center."""
        first_names = cast(_ManyToManySetter["FirstName"], self.first_names)
        last_names = cast(_ManyToManySetter["LastName"], self.last_names)
        center_first_names = cast(_ManyToManyRows["FirstName"], center.first_names)
        center_last_names = cast(_ManyToManyRows["LastName"], center.last_names)
        first_names.set(center_first_names.all())
        last_names.set(center_last_names.all())
        if save:
            self.save()

    def update_flags_by_pdf_type(
        self, pdf_type: _ReportReaderPdfType, save: bool = True
    ) -> None:
        """Updates the line identification flags based on a specific PdfType."""
        self.patient_info_line_flag = pdf_type.patient_info_line
        self.endoscope_info_line_flag = pdf_type.endoscope_info_line
        self.examiner_info_line_flag = pdf_type.examiner_info_line
        cut_off_below = cast(_ManyToManySetter["ReportReaderFlag"], self.cut_off_below)
        cut_off_above = cast(_ManyToManySetter["ReportReaderFlag"], self.cut_off_above)
        cut_off_below.set(pdf_type.cut_off_below_lines.all())
        cut_off_above.set(pdf_type.cut_off_above_lines.all())
        if save:
            self.save()

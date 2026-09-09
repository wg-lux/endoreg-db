from __future__ import annotations
from typing import Any


from django.db import models

from lx_dtypes.models.contracts.pdf_meta import PdfMetaPayload, PdfTypeSummaryPayload

from ..media.pdf.report_reader.report_reader_flag import ReportReaderFlag


def _flag_value(flag: ReportReaderFlag | None) -> str:
    if flag is None:
        raise ValueError("ReportReaderFlag is not set")
    return flag.value


class PdfType(models.Model):
    name: models.CharField[Any, Any] = models.CharField(max_length=255)

    patient_info_line: models.ForeignKey[Any] = models.ForeignKey(
        ReportReaderFlag,
        related_name="pdf_type_patient_info_line",
        on_delete=models.CASCADE,
    )
    endoscope_info_line: models.ForeignKey[Any] = models.ForeignKey(
        ReportReaderFlag,
        related_name="pdf_type_endoscopy_info_line",
        on_delete=models.CASCADE,
    )
    examiner_info_line: models.ForeignKey[Any] = models.ForeignKey(
        ReportReaderFlag,
        related_name="pdf_type_examiner_info_line",
        on_delete=models.CASCADE,
    )
    cut_off_above_lines: models.ManyToManyField[
        ReportReaderFlag,
        ReportReaderFlag,
    ] = models.ManyToManyField(
        ReportReaderFlag,
        related_name="pdf_type_cut_off_above_lines",
    )
    cut_off_below_lines: models.ManyToManyField[
        ReportReaderFlag,
        ReportReaderFlag,
    ] = models.ManyToManyField(
        ReportReaderFlag,
        related_name="pdf_type_cut_off_below_lines",
    )

    def __str__(self) -> str:
        summary = f"{self.name}"
        summary += f"\nPatient Info Line: {_flag_value(self.patient_info_line)}"
        summary += f"\nEndoscope Info Line: {_flag_value(self.endoscope_info_line)}"
        summary += f"\nExaminer Info Line: {_flag_value(self.examiner_info_line)}"
        summary += f"\nCut Off Above Lines: {[_.value for _ in self.cut_off_above_lines.all()]}"
        summary += f"\nCut Off Below Lines: {[_.value for _ in self.cut_off_below_lines.all()]}"
        return summary

    @classmethod
    def default_pdf_type(cls) -> "PdfType":
        return cls.objects.get(name="ukw-endoscopy-examination-report-generic")

    def to_summary_payload(self) -> PdfTypeSummaryPayload:
        return PdfTypeSummaryPayload(
            name=self.name,
            patient_info_line=_flag_value(self.patient_info_line),
            endoscope_info_line=_flag_value(self.endoscope_info_line),
            examiner_info_line=_flag_value(self.examiner_info_line),
            cut_off_above_lines=[item.value for item in self.cut_off_above_lines.all()],
            cut_off_below_lines=[item.value for item in self.cut_off_below_lines.all()],
        )


class PdfMeta(models.Model):
    pdf_type: models.ForeignKey[Any] = models.ForeignKey(
        PdfType, on_delete=models.CASCADE
    )
    date: models.DateField[Any, Any] = models.DateField()
    time: models.TimeField[Any, Any] = models.TimeField()
    pdf_hash: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)

    def __str__(self) -> str:
        return str(self.pdf_hash)

    @classmethod
    def create_from_file(cls, pdf_file: object) -> "PdfMeta":
        raise NotImplementedError("PdfMeta.create_from_file is not implemented.")

    def to_payload(self) -> PdfMetaPayload:
        return PdfMetaPayload(
            pdf_type=self.pdf_type.name,
            date=self.date,
            time=self.time,
            pdf_hash=self.pdf_hash,
        )

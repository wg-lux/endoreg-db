from typing import TYPE_CHECKING, cast

from django.core.files import File
from django.db import models

if TYPE_CHECKING:
    from ..media.pdf.report_reader.report_reader_flag import ReportReaderFlag


class PdfType(models.Model):
    """
    Defines a specific type or format of PDF report, linking to flags used for parsing.

    Used to configure how different PDF report layouts are processed.
    """

    name = models.CharField(max_length=255)

    patient_info_line = models.ForeignKey("ReportReaderFlag", related_name="pdf_type_patient_info_line", on_delete=models.CASCADE)
    endoscope_info_line = models.ForeignKey(
        "ReportReaderFlag",
        related_name="pdf_type_endoscopy_info_line",
        on_delete=models.CASCADE,
    )
    examiner_info_line = models.ForeignKey("ReportReaderFlag", related_name="pdf_type_examiner_info_line", on_delete=models.CASCADE)
    cut_off_above_lines = models.ManyToManyField(
        "ReportReaderFlag",
        related_name="pdf_type_cut_off_above_lines",
    )
    cut_off_below_lines = models.ManyToManyField(
        "ReportReaderFlag",
        related_name="pdf_type_cut_off_below_lines",
    )

    if TYPE_CHECKING:
        patient_info_line: models.ForeignKey["ReportReaderFlag"]
        endoscope_info_line: models.ForeignKey["ReportReaderFlag"]
        examiner_info_line: models.ForeignKey["ReportReaderFlag"]

        cut_off_above_lines = cast(models.manager.RelatedManager["ReportReaderFlag"], cut_off_above_lines)
        cut_off_below_lines = cast(models.manager.RelatedManager["ReportReaderFlag"], cut_off_below_lines)

    def __str__(self):
        """Returns a string summary of the PDF type and its associated flags."""
        summary = f"{self.name}"
        # add lines to summary
        summary += f"\nPatient Info Line: {self.patient_info_line.value}"
        summary += f"\nEndoscope Info Line: {self.endoscope_info_line.value}"
        summary += f"\nExaminer Info Line: {self.examiner_info_line.value}"
        summary += f"\nCut Off Above Lines: {[_.value for _ in self.cut_off_above_lines.all()]}"
        summary += f"\nCut Off Below Lines: {[_.value for _ in self.cut_off_below_lines.all()]}"

        return summary

    @classmethod
    def default_pdf_type(cls):
        """Returns a default PdfType instance, typically used as a fallback."""
        return PdfType.objects.get(name="ukw-endoscopy-examination-report-generic")


class PdfMeta(models.Model):
    """
    Stores metadata associated with a specific PDF document file.
    """

    pdf_type = models.ForeignKey(PdfType, on_delete=models.CASCADE)
    date = models.DateField()
    time = models.TimeField()
    pdf_hash = models.CharField(max_length=255, unique=True)

    def __str__(self):
        """Returns the PDF hash as its string representation."""
        return str(self.pdf_hash)

    @classmethod
    def create_from_file(cls, pdf_file):
        """
        Creates a PdfMeta instance from a PDF file object.
        Note: This implementation seems incomplete; it doesn't extract hash, date, time, or type.
        """
        pdf_file = File(pdf_file)
        pdf_meta = cls(file=pdf_file)
        pdf_meta.save()
        return pdf_meta

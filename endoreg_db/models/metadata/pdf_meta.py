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
        """
        String summary of the PDF type including the configured flag values.
        
        Returns:
            str: A multi-line string that starts with the PDF type name and lists the values of
                 patient info line, endoscope info line, examiner info line, cut-off-above lines,
                 and cut-off-below lines.
        """
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
        """
        String representation containing the PDF metadata hash.
        
        Returns:
            str: The value of the `pdf_hash` field.
        """
        return str(self.pdf_hash)

    @classmethod
    def create_from_file(cls, pdf_file):
        """
        Create and save a PdfMeta record from a PDF file.
        
        Parameters:
            pdf_file (IO|str): A file-like object or filesystem path pointing to the PDF document.
        
        Returns:
            PdfMeta: The saved PdfMeta instance corresponding to the provided file.
        
        Notes:
            This function does not extract or populate metadata such as pdf_hash, date, time, or pdf_type from the file.
        """
        pdf_file = File(pdf_file)
        pdf_meta = cls(file=pdf_file)
        pdf_meta.save()
        return pdf_meta
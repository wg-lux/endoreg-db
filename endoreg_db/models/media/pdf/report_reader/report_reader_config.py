# ReportReaderConfig Class
# Description: This class is used to store the configuration of the ReportReader

# PATIENT_INFO_LINE_FLAG = "Patient: "
# ENDOSCOPE_INFO_LINE_FLAG = "Gerät: "
# EXAMINER_INFO_LINE_FLAG = "1. Unters.:"
# CUT_OFF_BELOW_LINE_FLAG = "________________"


# CUT_OFF_ABOVE_LINE_FLAGS = [
#     ENDOSCOPE_INFO_LINE_FLAG,
#     EXAMINER_INFO_LINE_FLAG,
# ]

# CUT_OFF_BELOW_LINE_FLAGS = [
#         CUT_OFF_BELOW_LINE_FLAG
#     ]

from django.db import models


from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .report_reader_flag import ReportReaderFlag
    from ....administration.person import FirstName, LastName
    from ....administration.center import Center
    from ....metadata import PdfType

class ReportReaderConfig(models.Model):
    """
    Configuration settings for parsing PDF reports (ReportReader).

    Stores locale, name lists, date format, and flags used to identify key information lines
    and text sections to ignore.
    """
    locale = models.CharField(default="de_DE", max_length=10)
    first_names = models.ManyToManyField('FirstName', related_name='report_reader_configs')
    last_names = models.ManyToManyField('LastName', related_name='report_reader_configs')
    text_date_format = models.CharField(default = "%d.%m.%Y", max_length=10)
    patient_info_line_flag = models.ForeignKey("ReportReaderFlag", related_name='report_reader_configs_patient_info_line', on_delete=models.CASCADE)
    endoscope_info_line_flag = models.ForeignKey("ReportReaderFlag", related_name='report_reader_configs_endoscope_info_line', on_delete=models.CASCADE)
    examiner_info_line_flag = models.ForeignKey("ReportReaderFlag", related_name='report_reader_configs_examiner_info_line', on_delete=models.CASCADE)
    cut_off_below = models.ManyToManyField("ReportReaderFlag", related_name='report_reader_configs_cut_off_below')
    cut_off_above = models.ManyToManyField("ReportReaderFlag", related_name='report_reader_configs_cut_off_above')
    
    if TYPE_CHECKING:
        first_names: models.QuerySet["FirstName"]
        last_names: models.QuerySet["LastName"]
        patient_info_line_flag: "ReportReaderFlag"
        endoscope_info_line_flag: "ReportReaderFlag"
        examiner_info_line_flag: "ReportReaderFlag"
        cut_off_below: models.QuerySet["ReportReaderFlag"]
        cut_off_above: models.QuerySet["ReportReaderFlag"]
    

    def __str__(self):
        """Returns a string representation including the locale and primary key."""
        _str = f"ReportReaderConfig: {self.locale} (id: {self.pk}\n"
        return _str
    
    def update_names_by_center(self, center:"Center", save = True):
        """Updates the first and last name lists based on the names associated with a Center."""
        self.first_names.set(center.first_names.all())
        self.last_names.set(center.last_names.all())
        if save:
            self.save()

    def update_flags_by_pdf_type(self, pdf_type:"PdfType", save = True):
        """Updates the line identification flags based on a specific PdfType."""
        self.patient_info_line_flag = pdf_type.patient_info_line_flag
        self.endoscope_info_line_flag = pdf_type.endoscope_info_line_flag
        self.examiner_info_line_flag = pdf_type.examiner_info_line_flag
        self.cut_off_below.set(pdf_type.cut_off_below.all())
        self.cut_off_above.set(pdf_type.cut_off_above.all())
        if save:
            self.save()


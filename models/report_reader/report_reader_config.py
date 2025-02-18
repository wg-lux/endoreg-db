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
from .report_reader_flag import ReportReaderFlag
from ..center import Center
from ..data_file import PdfType

class ReportReaderConfig(models.Model):

    locale = models.CharField(default="de_DE", max_length=10)
    first_names = models.ManyToManyField('FirstName', related_name='report_reader_configs')
    last_names = models.ManyToManyField('LastName', related_name='report_reader_configs')
    text_date_format = models.CharField(default = "%d.%m.%Y", max_length=10)
    patient_info_line_flag = models.ForeignKey(ReportReaderFlag, related_name='report_reader_configs_patient_info_line', on_delete=models.CASCADE)
    endoscope_info_line_flag = models.ForeignKey(ReportReaderFlag, related_name='report_reader_configs_endoscope_info_line', on_delete=models.CASCADE)
    examiner_info_line_flag = models.ForeignKey(ReportReaderFlag, related_name='report_reader_configs_examiner_info_line', on_delete=models.CASCADE)
    cut_off_below = models.ManyToManyField(ReportReaderFlag, related_name='report_reader_configs_cut_off_below')
    cut_off_above = models.ManyToManyField(ReportReaderFlag, related_name='report_reader_configs_cut_off_above')
    
    def __str__(self):
        return self.locale
    
    def update_names_by_center(self, center:Center, save = True):
        self.first_names.set(center.first_names.all())
        self.last_names.set(center.last_names.all())
        if save:
            self.save()

    def update_flags_by_pdf_type(self, pdf_type:PdfType, save = True):
        self.patient_info_line_flag = pdf_type.patient_info_line_flag
        self.endoscope_info_line_flag = pdf_type.endoscope_info_line_flag
        self.examiner_info_line_flag = pdf_type.examiner_info_line_flag
        self.cut_off_below.set(pdf_type.cut_off_below.all())
        self.cut_off_above.set(pdf_type.cut_off_above.all())
        if save:
            self.save()


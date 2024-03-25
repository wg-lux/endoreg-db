from django.db import models

# import endoreg_center_id from django settings
from django.conf import settings


# import File class
from django.core.files import File

# # check if endoreg_center_id is set
# if not hasattr(settings, 'ENDOREG_CENTER_ID'):
#     ENDOREG_CENTER_ID = 9999
# else:
#     ENDOREG_CENTER_ID = settings.ENDOREG_CENTER_ID

class PdfType(models.Model):
    name = models.CharField(max_length=255)

    patient_info_line = models.ForeignKey(
        "ReportReaderFlag",
        related_name="pdf_type_patient_info_line", 
        on_delete=models.CASCADE
    )
    endoscope_info_line = models.ForeignKey(
        "ReportReaderFlag",
        related_name="pdf_type_endoscopy_info_line", 
        on_delete=models.CASCADE,
    )
    examiner_info_line = models.ForeignKey(
        "ReportReaderFlag",
        related_name="pdf_type_examiner_info_line", 
        on_delete=models.CASCADE
    )
    cut_off_above_lines = models.ManyToManyField(
        "ReportReaderFlag",
        related_name="pdf_type_cut_off_above_lines", 
    )
    cut_off_below_lines = models.ManyToManyField(
        "ReportReaderFlag",
        related_name="pdf_type_cut_off_below_lines", 
    )


    def __str__(self):
        summary = f"{self.name}"
        # add lines to summary
        summary += f"\nPatient Info Line: {self.patient_info_line.value}"
        summary += f"\nEndoscope Info Line: {self.endoscope_info_line.value}"
        summary += f"\nExaminer Info Line: {self.examiner_info_line.value}"
        summary += f"\nCut Off Above Lines: {[_.value for _ in self.cut_off_above_lines.all()]}"
        summary += f"\nCut Off Below Lines: {[_.value for _ in self.cut_off_below_lines.all()]}"
    
        return summary

class PdfMeta(models.Model):
    pdf_type = models.ForeignKey(PdfType, on_delete=models.CASCADE)
    date = models.DateField()
    time = models.TimeField()
    pdf_hash = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.pdf_hash

    @classmethod
    def create_from_file(cls, pdf_file):
        pdf_file = File(pdf_file)
        pdf_meta = cls(file=pdf_file)
        pdf_meta.save()
        return pdf_meta


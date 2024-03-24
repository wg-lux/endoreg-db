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

    def __str__(self):
        return self.name

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


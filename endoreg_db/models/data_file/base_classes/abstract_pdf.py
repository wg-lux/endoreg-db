# models/data_file/import_classes/raw_pdf.py
# django db model "RawPdf"
# Class to store raw pdf file using django file field
# Class contains classmethod to create object from pdf file
# objects contains methods to extract text, extract metadata from text and anonymize text from pdf file uzing agl_report_reader.ReportReader class
# ------------------------------------------------------------------------------

from django.db import models
from django.core.files.storage import FileSystemStorage
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from endoreg_db.utils.file_operations import get_uuid_filename
from icecream import ic

from agl_report_reader.report_reader import ReportReader

from endoreg_db.utils.hashs import get_pdf_hash
from ..metadata import SensitiveMeta

# setup logging to pdf_import.log
import logging

import shutil
from pathlib import Path

from ..base_classes.utils import (
    STORAGE_LOCATION,
)

logger = logging.getLogger("pdf_import")

RAW_PDF_DIR_NAME = "raw_pdf"
RAW_PDF_DIR = STORAGE_LOCATION / RAW_PDF_DIR_NAME

if not RAW_PDF_DIR.exists():
    RAW_PDF_DIR.mkdir(parents=True)


class AbstractPdfFile(models.Model):
    pdf_hash = models.CharField(max_length=255, unique=True)
    pdf_type = models.ForeignKey(
        "PdfType",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    center = models.ForeignKey(
        "Center",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )

    text = models.TextField(blank=True, null=True)

    raw_meta = models.JSONField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True

    def __str__(self):
        str_repr = f"RawPdfFile: {self.file.name}"
        return str_repr

    def delete_with_file(self):
        file_path = Path(self.file.path)
        if file_path.exists():
            file_path.unlink()
            logger.info(f"File removed: {file_path}")

        r = self.delete()
        return r

    def update(self, save=True, verbose=True):
        try:
            self.text, self.anonymized_text, self.raw_meta = self.process_file(
                verbose=verbose
            )
            self.state_report_processed = True
            self.state_report_processing_required = False

            if save:
                self.save()

            return True

        except Exception as e:
            logger.error(f"Error processing file: {self.file.path}")
            logger.error(e)
            return False

    def save(self, *args, **kwargs):
        if not self.file.name.endswith(".pdf"):
            raise ValidationError("Only PDF files are allowed")

        if not self.pdf_hash:
            self.pdf_hash = get_pdf_hash(self.file.path)

        super().save(*args, **kwargs)

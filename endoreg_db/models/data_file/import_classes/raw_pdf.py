# models/data_file/import_classes/raw_pdf.py
# django db model "RawPdf"
# Class to store raw pdf file using django file field
# Class contains classmethod to create object from pdf file
# objects contains methods to extract text, extract metadata from text and anonymize text from pdf file uzing agl_report_reader.ReportReader class
# ------------------------------------------------------------------------------

from django.db import models
from django.core.files.storage import FileSystemStorage
from django.core.files import File
from django.conf import settings
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from endoreg_db.utils.file_operations import get_uuid_filename

from agl_report_reader.report_reader import ReportReader

from endoreg_db.utils.hashs import get_pdf_hash
from ..metadata import SensitiveMeta

# setup logging to pdf_import.log
import logging
logger = logging.getLogger('pdf_import')

import shutil

# get pdf location from settings, default to ~/erc_data/raw_pdf and create if not exists
PSEUDO_DIR_RAW_PDF = getattr(settings, 'PSEUDO_DIR_RAW_PDF', settings.BASE_DIR / 'erc_data/raw_pdf')

class RawPdfFile(models.Model):
    file = models.FileField(
        upload_to='raw_pdf/',
        validators=[FileExtensionValidator(allowed_extensions=['pdf'])],
        storage=FileSystemStorage(location=PSEUDO_DIR_RAW_PDF.resolve().as_posix()),
    )

    pdf_hash = models.CharField(max_length=255, unique=True)
    pdf_type = models.ForeignKey('PdfType', on_delete=models.CASCADE)
    center = models.ForeignKey('Center', on_delete=models.CASCADE)

    state_report_processing_required = models.BooleanField(default = True)
    state_report_processed = models.BooleanField(default=False)

    # report_file = models.OneToOneField("ReportFile", on_delete=models.CASCADE, null=True, blank=True)
    sensitive_meta = models.OneToOneField(
        'SensitiveMeta',
        on_delete=models.CASCADE,
        related_name='raw_pdf_file',
        null=True,
        blank=True,
    )

    text = models.TextField(blank=True, null=True)
    anonymized_text = models.TextField(blank=True, null=True)

    raw_meta = models.JSONField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        str_repr = f"RawPdfFile: {self.file.name}"
        return str_repr

    @classmethod
    def create_from_file(
        cls,
        file_path,
        center_name,
        pdf_type_name, # to be depreceated / changed since we now import all pdfs from same directory
        destination_dir,
        save=True,
    ):
        from endoreg_db.models import PdfType, Center
        logger.info(f"Creating RawPdfFile object from file: {file_path}")
        original_file_name = file_path.name

        new_file_name, uuid = get_uuid_filename(file_path)

        if not destination_dir.exists():
            destination_dir.mkdir(parents=True)

        pdf_hash = get_pdf_hash(file_path)

        # check if pdf file already exists
        if cls.objects.filter(pdf_hash=pdf_hash).exists():
            logger.warning(f"RawPdfFile with hash {pdf_hash} already exists")
            return None
        
        assert pdf_type_name is not None, "pdf_type_name is required"
        assert center_name is not None, "center_name is required"

        pdf_type = PdfType.objects.get(name=pdf_type_name)
        center = Center.objects.get(name=center_name)

        new_file_path = destination_dir / new_file_name

        logger.info(f"Copying file to {new_file_path}")
        success = shutil.copy(file_path, new_file_path)
         
        # validate copy operation by comparing hashs
        assert get_pdf_hash(new_file_path) == pdf_hash, "Copy operation failed"

        raw_pdf = cls(
            file=new_file_path.resolve().as_posix(),
            pdf_hash=pdf_hash,
            pdf_type=pdf_type,
            center=center,
        )
        logger.info(f"RawPdfFile object created: {raw_pdf}")

        # remove source file
        file_path.unlink()
        logger.info(f"Source file removed: {file_path}")

        if save:
            raw_pdf.save()
        

        return raw_pdf

    def process_file(self, verbose = False):
        
        pdf_path = self.file.path
        rr_config = self.get_report_reader_config()

        rr = ReportReader(**rr_config) #FIXME In future we need to pass a configuration file 
        # This configuration file should be associated with pdf type 

        text, anonymized_text, report_meta = rr.process_report(pdf_path, verbose=verbose)
        if not self.sensitive_meta:
            sensitive_meta = SensitiveMeta.create_from_dict(report_meta)
            sensitive_meta.save()
            self.sensitive_meta = sensitive_meta

        else: 
            # update existing sensitive meta
            sensitive_meta = self.sensitive_meta
            sensitive_meta.update_from_dict(report_meta)

        return text, anonymized_text, report_meta
    
    def update(self, save=True, verbose = True):
        try:
            self.text, self.anonymized_text, self.raw_meta = self.process_file(verbose = verbose)
            self.state_report_processed = True
            self.state_report_processing_required = False
        
            if save: 
    
                self.save()

            return True

        except:
            logger.error(f"Error processing file: {self.file.path}")
            return False

    def save(self, *args, **kwargs):
        if not self.file.name.endswith('.pdf'):
            raise ValidationError('Only PDF files are allowed')
        
        if not self.pdf_hash:
            self.pdf_hash = get_pdf_hash(self.file.path)

        super().save(*args, **kwargs)


    def get_report_reader_config(self):
        if self.pdf_type.endoscope_info_line:
            endoscope_info_line = self.pdf_type.endoscope_info_line.value
        else:
            endoscope_info_line = None
        settings_dict = {
            "locale": "de_DE",
            "employee_first_names": [_.name for _ in self.center.first_names.all()],
            "employee_last_names": [_.name for _ in self.center.last_names.all()],
            "text_date_format":'%d.%m.%Y',
            "flags": {
                "patient_info_line": self.pdf_type.patient_info_line.value,
                "endoscope_info_line": endoscope_info_line,
                "examiner_info_line": self.pdf_type.examiner_info_line.value,
                "cut_off_below": [_.value for _ in self.pdf_type.cut_off_below_lines.all()],
                "cut_off_above": [_.value for _ in self.pdf_type.cut_off_above_lines.all()],
            }
        }

        return settings_dict

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
from ..base_classes.abstract_pdf import AbstractPdfFile

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


class RawPdfFile(AbstractPdfFile):
    file = models.FileField(
        upload_to=f"{RAW_PDF_DIR_NAME}/",
        validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
        storage=FileSystemStorage(location=STORAGE_LOCATION.resolve().as_posix()),
    )

    patient = models.ForeignKey(
        "Patient",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="raw_pdf_files",
    )

    state_report_processing_required = models.BooleanField(default=True)
    state_report_processed = models.BooleanField(default=False)
    raw_meta = models.JSONField(blank=True, null=True)
    # report_file = models.OneToOneField("ReportFile", on_delete=models.CASCADE, null=True, blank=True)
    sensitive_meta = models.ForeignKey(
        "SensitiveMeta",
        on_delete=models.SET_NULL,
        related_name="raw_pdf_files",
        null=True,
        blank=True,
    )

    report_file = models.ForeignKey(
        "ReportFile",
        on_delete=models.SET_NULL,
        related_name="raw_pdf_files",
        null=True,
        blank=True,
    )

    anonymized_text = models.TextField(blank=True, null=True)

    @classmethod
    def create_from_file(
        cls,
        file_path: Path,
        center_name,
        save=True,
        delete_source=True,
    ):
        from endoreg_db.models import Center

        logger.info(f"Creating RawPdfFile object from file: {file_path}")
        ic(f"Creating RawPdfFile object from file: {file_path}")

        new_file_name, uuid = get_uuid_filename(file_path)

        pdf_hash = get_pdf_hash(file_path)
        ic(pdf_hash)
        new_file_path = RAW_PDF_DIR / new_file_name
        # check if pdf file already exists

        if cls.objects.filter(pdf_hash=pdf_hash).exists():
            existing_pdf_file = cls.objects.filter(pdf_hash=pdf_hash).get()
            logger.warning(f"RawPdfFile with hash {pdf_hash} already exists")
            ic(f"RawPdfFile with hash {pdf_hash} already exists")

            existing_pdf_file.verify_existing_file(fallback_file=file_path)

            return existing_pdf_file

        else:
            ic(f"No existing pdf file found for hash {pdf_hash}")

        # assert pdf_type_name is not None, "pdf_type_name is required"
        assert center_name is not None, "center_name is required"

        # pdf_type = PdfType.objects.get(name=pdf_type_name)
        center = Center.objects.get(name=center_name)

        logger.info(f"Copying file to {new_file_path}")
        ic(f"Copying file to {new_file_path}")
        _success = shutil.copy(file_path, new_file_path)

        # validate copy operation by comparing hashs
        assert get_pdf_hash(new_file_path) == pdf_hash, "Copy operation failed"

        raw_pdf = cls(
            file=new_file_path.resolve().as_posix(),
            pdf_hash=pdf_hash,
            # pdf_type=pdf_type,
            center=center,
        )
        raw_pdf.save()
        logger.info(f"RawPdfFile object created: {raw_pdf}")
        ic(f"RawPdfFile object created: {raw_pdf}")

        # remove source file
        if delete_source:
            file_path.unlink()
            logger.info(f"Source file removed: {file_path}")
            ic(f"Source file removed: {file_path}")

        if save:
            raw_pdf.save()

        return raw_pdf

    def save(self, *args, **kwargs):
        if not self.file.name.endswith(".pdf"):
            raise ValidationError("Only PDF files are allowed")

        if not self.pdf_hash:
            self.pdf_hash = get_pdf_hash(self.file.path)

        super().save(*args, **kwargs)

    def verify_existing_file(self, fallback_file):
        if not Path(self.file.path).exists():
            logger.warning(f"File not found: {self.file.path}")
            logger.warning(f"Using fallback file: {fallback_file}")
            ic(f"File not found: {self.file.path}")
            ic(f"Copy fallback file: {fallback_file} to existing filepath")

            shutil.copy(fallback_file, self.file.path)

            self.save()

    def process_file(self, verbose=False):
        pdf_path = self.file.path
        rr_config = self.get_report_reader_config()

        rr = ReportReader(
            **rr_config
        )  # FIXME In future we need to pass a configuration file
        # This configuration file should be associated with pdf type

        text, anonymized_text, report_meta = rr.process_report(
            pdf_path, verbose=verbose
        )

        self.text = text
        self.anonymized_text = anonymized_text

        report_meta["center_name"] = self.center.name
        if not self.sensitive_meta:
            sensitive_meta = SensitiveMeta.create_from_dict(report_meta)
            self.sensitive_meta = sensitive_meta

        else:
            # update existing sensitive meta
            sensitive_meta = self.sensitive_meta
            sensitive_meta.update_from_dict(report_meta)

        self.raw_meta = report_meta

        sensitive_meta.save()
        self.save()

        return text, anonymized_text, report_meta

    def get_report_reader_config(self):
        from endoreg_db.models import PdfType, Center
        from warnings import warn

        if not self.pdf_type:
            warn("PdfType not set, using default settings")
            pdf_type = PdfType.default_pdf_type()
        else:
            pdf_type: PdfType = self.pdf_type
        center: Center = self.center
        if pdf_type.endoscope_info_line:
            endoscope_info_line = pdf_type.endoscope_info_line.value

        else:
            endoscope_info_line = None
        settings_dict = {
            "locale": "de_DE",
            "employee_first_names": [_.name for _ in center.first_names.all()],
            "employee_last_names": [_.name for _ in center.last_names.all()],
            "text_date_format": "%d.%m.%Y",
            "flags": {
                "patient_info_line": pdf_type.patient_info_line.value,
                "endoscope_info_line": endoscope_info_line,
                "examiner_info_line": pdf_type.examiner_info_line.value,
                "cut_off_below": [_.value for _ in pdf_type.cut_off_below_lines.all()],
                "cut_off_above": [_.value for _ in pdf_type.cut_off_above_lines.all()],
            },
        }

        return settings_dict

    def get_or_create_report_file(self):
        from endoreg_db.models import ReportFile

        if self.report_file:
            report_file = self.report_file

        elif ReportFile.objects.filter(pdf_hash=self.pdf_hash).exists():
            report_file = ReportFile.objects.filter(pdf_hash=self.pdf_hash).get()
            self.report_file = report_file
            self.save()
        else:
            # TODO  Make sure all required states are set
            patient = self.sensitive_meta.get_or_create_pseudo_patient()
            examiner = self.sensitive_meta.get_or_create_pseudo_examiner()
            patient_examination = (
                self.sensitive_meta.get_or_create_pseudo_patient_examination()
            )

            report_file = ReportFile.objects.create(
                pdf_hash=self.pdf_hash,
                center=self.center,
                sensitive_meta=self.sensitive_meta,
                patient=patient,
                examiner=examiner,
                examination=patient_examination,
                text=self.anonymized_text,
            )

            report_file.save()
            self.report_file = report_file
            self.save()

        return report_file

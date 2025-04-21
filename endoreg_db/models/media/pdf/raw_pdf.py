# models/data_file/import_classes/raw_pdf.py
# django db model "RawPdf"
# Class to store raw pdf file using django file field
# Class contains classmethod to create object from pdf file
# objects contains methods to extract text, extract metadata from text and anonymize text from pdf file uzing agl_report_reader.ReportReader class
# ------------------------------------------------------------------------------

from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from endoreg_db.utils.file_operations import get_uuid_filename
from icecream import ic
from typing import TYPE_CHECKING
from ...utils import FILE_STORAGE, PDF_DIR
from endoreg_db.utils.hashs import get_pdf_hash

if TYPE_CHECKING:
    from endoreg_db.models.administration.person import (
        Patient,
        Examiner,
    )
    from .report_file import AnonymExaminationReport
    from ...medical.patient import PatientExamination
    from ...administration import Center
    from ...metadata.pdf_meta import PdfType
from ...metadata import SensitiveMeta

# setup logging to pdf_import.log
import logging

import shutil
from pathlib import Path

from ...utils import data_paths

logger = logging.getLogger("pdf_import")

class RawPdfFile(models.Model):
    # Fields from AbstractPdfFile
    pdf_hash = models.CharField(max_length=255, unique=True)
    pdf_type = models.ForeignKey(
        "PdfType",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    center = models.ForeignKey(
        "Center",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    examination = models.ForeignKey(
        "PatientExamination",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="raw_pdf_files",
    )
    examiner = models.ForeignKey(
        "Examiner",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    text = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Fields specific to RawPdfFile (keeping existing related_names)
    file = models.FileField(
        upload_to=PDF_DIR,
        validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
        storage=FILE_STORAGE,
    )
    patient = models.ForeignKey(
        "Patient",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="raw_pdf_files",
    )
    sensitive_meta = models.ForeignKey(
        "SensitiveMeta",
        on_delete=models.SET_NULL,
        related_name="raw_pdf_files",
        null=True,
        blank=True,
    )
    state_report_processing_required = models.BooleanField(default=True)
    state_report_processed = models.BooleanField(default=False)
    raw_meta = models.JSONField(blank=True, null=True)
    anonym_examination_report = models.OneToOneField(
        "AnonymExaminationReport",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="raw_pdf_file",
    )
    anonymized_text = models.TextField(blank=True, null=True)

    # Type hinting if needed
    if TYPE_CHECKING:
        pdf_type: "PdfType"
        examination: "PatientExamination"
        examiner: "Examiner"
        patient: "Patient"
        center: "Center"
        anonym_examination_report: "AnonymExaminationReport"
        sensitive_meta: "SensitiveMeta"

    def __str__(self):
        str_repr = f"{self.pdf_hash} ({self.pdf_type}, {self.center})"
        return str_repr

    def delete_with_file(self):
        file_path = Path(self.file.path)
        if file_path.exists():
            file_path.unlink()
            logger.info("File removed: %s", file_path)

        r = self.delete()
        return r

    @classmethod
    def create_from_file(
        cls,
        file_path: Path,
        center_name,
        save=True,
        delete_source=True,
    ):
        from endoreg_db.models.administration import Center

        new_file_name, _uuid = get_uuid_filename(file_path)

        pdf_hash = get_pdf_hash(file_path)
        ic(pdf_hash)
        new_file_path = data_paths["raw_report"] / new_file_name
        # check if pdf file already exists

        if cls.objects.filter(pdf_hash=pdf_hash).exists():
            existing_pdf_file = cls.objects.filter(pdf_hash=pdf_hash).get()
            logger.warning("RawPdfFile with hash %s already exists", pdf_hash)

            existing_pdf_file.verify_existing_file(fallback_file=file_path)

            return existing_pdf_file


        assert center_name is not None, "center_name is required"

        center = Center.objects.get(name=center_name)

        ic(f"Copying file to {new_file_path}")
        _success = shutil.copy(file_path, new_file_path)

        # validate copy operation by comparing hashs
        assert get_pdf_hash(new_file_path) == pdf_hash, "Copy operation failed"

        raw_pdf = cls(
            file=new_file_path.as_posix(),
            pdf_hash=pdf_hash,
            center=center,
        )
        raw_pdf.save()

        # remove source file
        if delete_source:
            file_path.unlink()

        if save:
            raw_pdf.save()

        return raw_pdf

    def save(self, *args, **kwargs):
        if not self.file.name.endswith(".pdf"):
            raise ValidationError("Only PDF files are allowed")

        if not self.pdf_hash and self.file:
            try:
                self.pdf_hash = get_pdf_hash(self.file.path)
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as e:
                logger.error("Could not calculate hash for %s: %s", self.file.name, e)

        if not self.patient and self.sensitive_meta:
            self.patient = self.sensitive_meta.pseudo_patient
        if not self.examination and self.sensitive_meta:
            self.examination = self.sensitive_meta.pseudo_examination
        if not self.center and self.sensitive_meta:
            self.center = self.sensitive_meta.center
        if not self.examiner and self.sensitive_meta and hasattr(self.sensitive_meta, 'pseudo_examiner'):
            self.examiner = self.sensitive_meta.pseudo_examiner

        super().save(*args, **kwargs)

        if not self.pdf_hash and self.file and Path(self.file.path).exists():
            try:
                self.pdf_hash = get_pdf_hash(self.file.path)
                super().save(update_fields=['pdf_hash'])
            except (OSError, ValueError) as e:
                logger.error("Could not calculate hash after save for %s: %s", self.file.name, e)

    def verify_existing_file(self, fallback_file):
        if not Path(self.file.path).exists():
            shutil.copy(fallback_file, self.file.path)
            self.save()

    def process_file(self, text, anonymized_text, report_meta, verbose):
        self.text = text
        self.anonymized_text = anonymized_text

        report_meta["center_name"] = self.center.name
        if not self.sensitive_meta:
            sensitive_meta = SensitiveMeta.create_from_dict(report_meta)
            self.sensitive_meta = sensitive_meta

        else:
            sensitive_meta = self.sensitive_meta
            sensitive_meta.update_from_dict(report_meta)

        self.raw_meta = report_meta

        sensitive_meta.save()
        self.save()

        return text, anonymized_text, report_meta

    def get_report_reader_config(self):
        from ...administration import Center
        from ...metadata.pdf_meta import PdfType
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

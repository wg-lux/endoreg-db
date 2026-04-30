# models/data_file/import_classes/raw_pdf.py
# django db model "RawPdf"
# Class to store raw pdf file using django file field
# Class contains classmethod to create object from pdf file
# objects contains methods to extract text, extract metadata from text and anonymize text from pdf file uzing agl_report_reader.ReportReader class
# ------------------------------------------------------------------------------
import uuid
from typing import TYPE_CHECKING, Optional, Any, cast, Union

from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.validators import FileExtensionValidator
from django.db import models
from django.urls import reverse
from endoreg_db.models.media.pdf.create_report_from_file import _create_from_file
from endoreg_db.utils.hashs import get_pdf_hash
from endoreg_db.utils.paths import (
    ANONYM_REPORT_DIR,
    IMPORT_REPORT_DIR,
    SENSITIVE_REPORT_DIR,
)
from endoreg_db.utils.encryption.encrypted import LazyEncryptedStorage
from endoreg_db.utils.storage import (
    delete_field_file,
    ensure_local_file,
    file_exists,
    save_local_file,
)
from endoreg_db.utils.storage_streaming import maybe_local_plaintext_path
from endoreg_db.utils.storage_profile import (
    PayloadKind,
    StoragePolicy,
    resolve_storage_policy,
)

if TYPE_CHECKING:
    from django.db.models.fields.files import FieldFile

    from endoreg_db.models.state import RawPdfState

import logging
from pathlib import Path

from ...metadata import SensitiveMeta

logger = logging.getLogger("raw_pdf")


class RawPdfFile(models.Model):
    objects = models.Manager()
    # Fields from AbstractPdfFile
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
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
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)

    file = models.FileField(
        # Use the relative path from the specific REPORT_DIR
        upload_to=SENSITIVE_REPORT_DIR.name,
        storage=LazyEncryptedStorage(),
        validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
    )
    processed_file = models.FileField(
        upload_to=ANONYM_REPORT_DIR.name,
        storage=LazyEncryptedStorage(),
        validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
        null=True,
        blank=True,
    )
    state = models.OneToOneField(
        "RawPdfState",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="raw_pdf_file",
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

    # Type hinting is needed, improve and use correct django types
    if TYPE_CHECKING:
        from endoreg_db.models import (
            AnonymExaminationReport,
            Center,
            Examiner,
            Patient,
            PatientExamination,
            RawPdfState,
            SensitiveMeta,
        )

        center: models.ForeignKey["Center | None"]
        examination: models.ForeignKey["PatientExamination | None"]
        examiner: models.ForeignKey["Examiner | None"]
        state: models.ForeignKey["RawPdfState | None"]
        patient: models.ForeignKey["Patient | None"]
        sensitive_meta: models.ForeignKey["SensitiveMeta | None"]
        anonym_examination_report: models.OneToOneField[
            "AnonymExaminationReport | None"
        ]
        file = cast(FieldFile, file)
        processed_file = cast(FieldFile, processed_file)

    @property
    def storage_policy(self) -> StoragePolicy:
        return resolve_storage_policy(PayloadKind.REPORT_PDF)

    @property
    def uses_app_encrypted_storage(self) -> bool:
        return self.storage_policy == StoragePolicy.APP_ENCRYPTED

    @property
    def file_path(self) -> Path | None:
        """
        Deprecated: return a local plaintext path only when one is explicitly available.

        Use ensure_local_file(self.file) for tooling that requires a real path.
        """
        return maybe_local_plaintext_path(self.file)

    def set_file_path(self, file_path: Path):
        """
        Sets the file path of the stored report file.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File path does not exist: {file_path}")

        save_local_file(self.file, file_path, name=file_path.name, save=False)
        self.save(update_fields=["file"])

    @property
    def anonymized_file_path(self) -> Path | None:
        """
        Deprecated: return a local plaintext path only when one is explicitly available.

        Use ensure_local_file(self.processed_file) for tooling that requires a real path.
        """
        return maybe_local_plaintext_path(self.processed_file)

    def set_anonymized_file_path(self, file_path: Path):
        """
        Sets the file path of the anonymized report file.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File path does not exist: {file_path}")

        save_local_file(self.processed_file, file_path, name=file_path.name, save=False)
        self.save(update_fields=["processed_file"])

    def get_raw_file_path(self) -> Optional[Path]:
        """
        Get the path to the raw report file, searching common locations.

        This method attempts to find the original raw report file by checking:
        1. Checking the file field if it already points to a valid file
        2. Direct hash-based path in import/report_import or sensitive_reports
        3. Scanning canonical report directories for files matching the hash

        Returns:
            Path to raw file if it exists, None otherwise
        """
        # Check if file field already points to a valid file
        file_path = self.file_path
        if file_path is not None and file_path.exists():
            logger.debug("Found raw report via explicit local path: %s", file_path)
            return file_path

        # Canonical raw report lookup order.
        raw_dirs = [
            SENSITIVE_REPORT_DIR,  # Files might be in sensitive dir
            IMPORT_REPORT_DIR,  # General report directory
        ]

        # Check direct hash-based name in each directory
        for raw_dir in raw_dirs:
            if not raw_dir.exists():
                continue

            hash_path = raw_dir / f"{self.pdf_hash}.pdf"
            if hash_path.exists():
                logger.debug(f"Found raw report at: {hash_path}")
                return hash_path

        # Scan directories for matching hash
        for raw_dir in raw_dirs:
            if not raw_dir.exists():
                continue

            for file_path in raw_dir.glob("*.pdf"):
                try:
                    file_hash = get_pdf_hash(file_path)
                    if file_hash == self.pdf_hash:
                        logger.debug(f"Found matching report by hash: {file_path}")
                        return file_path
                except Exception as e:
                    logger.debug(f"Error checking {file_path}: {e}")
                    continue

        logger.warning(f"No raw file found for report hash: {self.pdf_hash}")
        return None

    @property
    def file_url(self) -> Any | str | None:
        """
        Returns the URL of the stored report file if available; otherwise, returns None.
        """
        try:
            if not self.file or not self.file.name or self.pk is None:
                return None
            return reverse("api:pdf-stream", kwargs={"pk": self.pk})
        except (ValueError, AttributeError):
            return None

    @property
    def anonymized_file_url(self):
        """
        Returns the URL of the stored report file if available; otherwise, returns None.
        """
        try:
            if (
                not self.processed_file
                or not self.processed_file.name
                or self.pk is None
            ):
                return None
            stream_url = reverse("api:pdf-stream", kwargs={"pk": self.pk})
            return f"{stream_url}?type=processed"
        except (ValueError, AttributeError):
            return None

    def __str__(self):
        """
        Return a string representation of the RawPdfFile, including its report hash, type, and center.
        """
        str_repr = f"{self.pdf_hash} ({self.pdf_type}, {self.center})"
        return str_repr

    def delete(self, *args, **kwargs):
        """
        Deletes the RawPdfFile instance from the database and removes the associated file from storage if it exists.

        This method ensures that the physical report file is deleted from the file system after the database record is removed. Logs warnings or errors if the file cannot be found or deleted.
        """
        primary_name = self.file.name if self.file and self.file.name else None
        anonymized_name = (
            self.processed_file.name
            if self.processed_file and self.processed_file.name
            else None
        )

        if delete_field_file(self, "file", missing_ok=True, save=False):
            logger.info("Original file removed from storage: %s", primary_name)
        if delete_field_file(self, "processed_file", missing_ok=True, save=False):
            logger.info("Anonymized file removed from storage: %s", anonymized_name)

        super().delete(*args, **kwargs)

        # --- Convenience state/meta helpers used in tests and admin workflows ---

    def mark_sensitive_meta_processed(self, *, save: bool = True) -> "RawPdfFile":
        """
        Mark this video's processing state as having its sensitive meta fully processed.
        This proxies to the related VideoState and persists by default.
        """
        sm = self.sensitive_meta
        from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

        if not isinstance(sm, SensitiveMeta):
            raise AttributeError()
        state = self.get_or_create_state()
        state.mark_sensitive_meta_processed(save=save)
        return self

    def mark_sensitive_meta_verified(self) -> "RawPdfFile":
        """
        Mark the associated SensitiveMeta as verified by setting both DOB and names as verified.
        Ensures the SensitiveMeta and its state exist.
        """
        sm = self.sensitive_meta
        # Use SensitiveMeta methods to update underlying SensitiveMetaState
        from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

        if not isinstance(sm, SensitiveMeta):
            raise AttributeError()

        sm.mark_dob_verified()
        sm.mark_names_verified()
        return self

    def validate_metadata_annotation(
        self, extracted_data_dict: Optional[dict] = None
    ) -> bool:
        """
        Validate the metadata of the RawPdf instance.

        Called after annotation in the frontend, this method deletes the associated active file, updates the sensitive meta data with the user annotated data.
        It also ensures the video file is properly saved after the metadata update.
        """

        if not extracted_data_dict:
            logger.error("No extracted data provided for validation.")
            return False

        sensitive_meta = self.sensitive_meta
        if sensitive_meta is None:
            logger.error("No sensitive meta attached to report %s.", self.pk)
            return False

        if extracted_data_dict:
            sensitive_meta.update_from_dict(extracted_data_dict)
        else:
            return False

        # Save the sensitive meta to ensure changes are persisted
        sensitive_meta.save()

        # Save the RawPdfFile instance to ensure all changes are saved
        self.save()

        logger.info(
            f"Metadata for report {self.pk} validated and updated successfully."
        )

        deleted_original = delete_field_file(self, "file", missing_ok=True, save=False)
        deleted_anonymized = delete_field_file(
            self, "processed_file", missing_ok=True, save=False
        )
        self.get_or_create_state().mark_anonymization_validated()

        if deleted_original or deleted_anonymized:
            self.save(
                update_fields=["file", "processed_file"]
            )  # Persist cleared fields

        self.mark_sensitive_meta_processed()
        self.mark_sensitive_meta_verified()

        logger.info(f"Files for report {self.pk} deleted successfully.")
        return True

    @classmethod
    def create_from_file(
        cls, file_path: Union[str, Path], center_name: Optional[str] = None, **kwargs
    ) -> "RawPdfFile":
        """
        Creates or retrieves a RawPdfFile instance.
        """
        return _create_from_file(
            cls_model=cls, file_path=file_path, center_name=center_name, **kwargs
        )

    @classmethod
    def create_from_file_initialized(
        cls, file_path: Union[str, Path], center_name: Optional[str] = None, **kwargs
    ) -> "RawPdfFile":
        """
        Creates a RawPdfFile and immediately ensures states and metadata are initialized.
        """
        raw_pdf = cls.create_from_file(
            file_path=file_path, center_name=center_name, **kwargs
        )
        return raw_pdf.initialize()

    def initialize(self) -> "RawPdfFile":
        """
        Initialize the RawPdfFile instance by ensuring related state exists and saving.
        Standardized to match VideoFile.initialize().
        """
        # Create a new state if it doesn't exist
        self.state = self.get_or_create_state()

        # If PDFs ever require extra metadata parsing upon init, it goes here.

        self.save(update_fields=["state"])
        return self

    def save(self, *args, **kwargs):
        # Ensure hash is calculated before the first save if possible and not already set
        # This is primarily a fallback if instance created manually without using create_from_file
        """
        Saves the RawPdfFile instance, ensuring the report hash is set and related fields are derived from metadata.

        If the report hash is missing, attempts to calculate it from the file before saving. Validates that the file has a `.pdf` extension. If related fields such as patient, examination, center, or examiner are unset but available in the associated sensitive metadata, they are populated accordingly before saving.
        """
        if not self.pk and not self.pdf_hash and self.file:
            try:
                with ensure_local_file(self.file) as local_path:
                    self.pdf_hash = get_pdf_hash(local_path)
                    logger.info(
                        "Calculated hash during pre-save for %s", self.file.name
                    )
            except Exception as exc:
                logger.warning(
                    "Could not calculate hash before initial save for %s: %s",
                    self.file.name,
                    exc,
                )

        if self.file and not self.file.name.endswith(".pdf"):
            raise ValidationError("Only report files are allowed")

        # If hash is still missing after potential creation logic (e.g., direct instantiation)
        # and the file exists in storage, try calculating it from storage path.
        # This is less ideal as it requires the file to be saved first.
        if not self.pdf_hash and self.pk and self.file and file_exists(self.file):
            try:
                with ensure_local_file(self.file) as local_path:
                    logger.warning(
                        "Hash missing for saved file %s. Recalculating.", self.file.name
                    )
                    self.pdf_hash = get_pdf_hash(local_path)
            except Exception as exc:
                logger.error(
                    "Could not calculate hash during save for existing file %s: %s",
                    self.file.name,
                    exc,
                )

        # Derive related fields from sensitive_meta if available
        if not self.patient and self.sensitive_meta:
            self.patient = self.sensitive_meta.pseudo_patient
        if not self.examination and self.sensitive_meta:
            self.examination = self.sensitive_meta.pseudo_examination
        if not self.center and self.sensitive_meta:
            self.center = self.sensitive_meta.center
        # TODO Outdated?
        # if not self.examiner and self.sensitive_meta and hasattr(self.sensitive_meta, 'pseudo_examiner'):
        #     self.examiner = self.sensitive_meta.pseudo_examiner

        super().save(*args, **kwargs)

    def get_or_create_state(self) -> "RawPdfState":
        """
        Retrieve the associated RawPdfState for this RawPdfFile, creating and linking a new one if none exists.

        Returns:
            RawPdfState: The existing or newly created RawPdfState instance linked to this RawPdfFile.
        """
        from endoreg_db.models.state import RawPdfState

        if self.state:
            return self.state

        # Create a new RawPdfState instance directly and assign it
        state = RawPdfState()
        state.save()
        self.state = state
        self.save(update_fields=["state"])  # Save the RawPdfFile to link the state
        logger.info("Created new RawPdfState for RawPdfFile %s", self.pk)
        return state

    def verify_existing_file(self, fallback_file):
        # This method might still be useful if called explicitly, but create_from_file now handles restoration
        # Ensure fallback_file is a Path object.
        """
        Checks if the stored report file exists in storage and attempts to restore it from a fallback file path if missing.

        Parameters:
            fallback_file: Path or string representing the fallback file location to restore from if the stored file is missing.
        """
        if not isinstance(fallback_file, Path):
            fallback_file = Path(fallback_file)

        _file = self.file
        assert _file is not None
        try:
            if not _file.field.storage.exists(_file.name):
                logger.warning(
                    f"File missing at storage path {_file.name}. Attempting copy from fallback {fallback_file}"
                )
                if fallback_file.exists():
                    with fallback_file.open("rb") as f:
                        # Use save method which handles storage backend
                        _file.save(
                            Path(_file.name).name, File(f), save=True
                        )  # Re-save the file content
                    logger.info(
                        f"Successfully restored file from fallback {fallback_file} to {_file.name}"
                    )
                else:
                    logger.error(f"Fallback file {fallback_file} does not exist.")
        except Exception as e:
            logger.error(f"Error during verify_existing_file for {_file.name}: {e}")

    def process_file(self, text, anonymized_text, report_meta, verbose):
        self.text = text
        self.anonymized_text = anonymized_text

        assert self.center is not None, "Center must be set before processing file"

        report_meta["center_name"] = self.center.name
        if not self.sensitive_meta:
            # Pass the original report_meta with date objects to SensitiveMeta logic
            sensitive_meta = SensitiveMeta.create_from_dict(report_meta)
            self.sensitive_meta = sensitive_meta
        else:
            sensitive_meta = self.sensitive_meta
            # Pass the original report_meta with date objects to SensitiveMeta logic
            sensitive_meta.update_from_dict(report_meta)

        # For storing in raw_meta (JSONField), dates need to be strings.
        # Create a serializable version of report_meta for raw_meta.
        import copy
        from datetime import date, datetime

        serializable_report_meta = copy.deepcopy(report_meta)
        for key, value in serializable_report_meta.items():
            if isinstance(value, (datetime, date)):
                serializable_report_meta[key] = value.isoformat()

        self.raw_meta = serializable_report_meta  # Assign the version with string dates

        sensitive_meta.save()  # Save SensitiveMeta first
        self.save()  # Then save RawPdfFile

        return text, anonymized_text, report_meta

    def get_report_reader_config(self):
        from warnings import warn

        from ...administration import Center
        from ...metadata.pdf_meta import PdfType

        _center = self.center
        assert _center is not None, "Center must be set to get report reader config"

        if not self.pdf_type:
            warn("PdfType not set, using default settings")
            pdf_type = PdfType.default_pdf_type()
        else:
            pdf_type: PdfType = self.pdf_type
        center: Center = _center
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

    @staticmethod
    def get_report_by_pk(pk: int) -> "RawPdfFile":
        try:
            return RawPdfFile.objects.get(pk=pk)
        except RawPdfFile.DoesNotExist:
            raise ValueError(f"report with ID {pk} does not exist.")

    @staticmethod
    def get_report_by_hash(hash: str) -> "RawPdfFile":
        try:
            return RawPdfFile.objects.get(pdf_hash=hash)
        except RawPdfFile.DoesNotExist:
            raise ValueError(f"report with ID {hash} does not exist.")

# models/data_file/import_classes/raw_pdf.py
# django db model "RawPdf"
# Class to store raw pdf file using django file field
# Class contains classmethod to create object from pdf file
# objects contains methods to extract text, extract metadata from text and anonymize text from pdf file uzing agl_report_reader.ReportReader class
# ------------------------------------------------------------------------------
from typing import TYPE_CHECKING, Optional, cast

from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.validators import FileExtensionValidator
from django.db import models

from endoreg_db.utils.file_operations import get_uuid_filename
from endoreg_db.utils.hashs import get_pdf_hash
from endoreg_db.utils.paths import PDF_DIR
from endoreg_db.utils.storage import (
    delete_field_file,
    ensure_local_file,
    file_exists,
    save_local_file,
)

if TYPE_CHECKING:
    from django.db.models.fields.files import FieldFile

    from endoreg_db.models.state import RawPdfState

# setup logging to pdf_import.log
import logging
from pathlib import Path

from ...metadata import SensitiveMeta

logger = logging.getLogger("raw_pdf")


class RawPdfFile(models.Model):
    objects = models.Manager()
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
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)
    anonymized = models.BooleanField(default=False, help_text="True if the PDF has been anonymized.")
    file = models.FileField(
        # Use the relative path from the specific PDF_DIR
        upload_to=PDF_DIR.name,
        validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
    )
    anonymized_file = models.FileField(
        upload_to=PDF_DIR.name,
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
        anonym_examination_report: models.OneToOneField["AnonymExaminationReport | None"]
        file = cast(FieldFile, file)
        anonymized_file = cast(FieldFile, anonymized_file)

    @property
    def uuid(self):
        """
        Compatibility property - returns pdf_hash as UUID-like identifier.

        Note: RawPdfFile uses pdf_hash instead of UUID for identification.
        This property exists for API backward compatibility.
        """
        return self.pdf_hash

    @property
    def file_path(self) -> Path | None:
        """
        Returns the file path of the stored PDF file if available; otherwise, returns None.
        """
        from django.db.models.fields.files import FieldFile

        # assert self.file has path attribute
        assert isinstance(self.file, FieldFile)
        if self.file and self.file.name:
            try:
                return Path(self.file.path)
            except (ValueError, AttributeError, NotImplementedError):
                return None
        return None

    def set_file_path(self, file_path: Path):
        """
        Sets the file path of the stored PDF file.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File path does not exist: {file_path}")

        save_local_file(self.file, file_path, name=file_path.name, save=False)
        self.save(update_fields=["file"])

    @property
    def anonymized_file_path(self) -> Path | None:
        """
        Returns the file path of the anonymized PDF file if available; otherwise, returns None.
        """
        if self.anonymized_file and self.anonymized_file.name:
            try:
                return Path(self.anonymized_file.path)
            except (ValueError, AttributeError, NotImplementedError):
                return None
        return None

    def set_anonymized_file_path(self, file_path: Path):
        """
        Sets the file path of the anonymized PDF file.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File path does not exist: {file_path}")

        save_local_file(self.anonymized_file, file_path, name=file_path.name, save=False)
        self.save(update_fields=["anonymized_file"])

    def get_raw_file_path(self) -> Optional[Path]:
        """
        Get the path to the raw PDF file, searching common locations.

        This method attempts to find the original raw PDF file by checking:
        1. Direct hash-based path in raw_pdfs/
        2. Scanning raw_pdfs/ directory for files matching the hash
        3. Checking the file field if it exists

        Returns:
            Path to raw file if it exists, None otherwise
        """
        from django.conf import settings

        # Check if file field already points to a valid file
        if self.file and self.file.name:
            try:
                file_path = Path(self.file.path)
                if file_path.exists():
                    logger.debug(f"Found raw PDF via file field: {file_path}")
                    return file_path
            except (ValueError, AttributeError, NotImplementedError):
                pass

        # Define potential raw directories
        raw_dirs = [
            PDF_DIR / "sensitive",  # Files might be in sensitive dir
            Path(settings.BASE_DIR) / "data" / "raw_pdfs",
            Path(settings.BASE_DIR) / "data" / "pdfs" / "raw",
            PDF_DIR,  # General PDF directory
        ]

        # Check direct hash-based name in each directory
        for raw_dir in raw_dirs:
            if not raw_dir.exists():
                continue

            hash_path = raw_dir / f"{self.pdf_hash}.pdf"
            if hash_path.exists():
                logger.debug(f"Found raw PDF at: {hash_path}")
                return hash_path

        # Scan directories for matching hash
        for raw_dir in raw_dirs:
            if not raw_dir.exists():
                continue

            for file_path in raw_dir.glob("*.pdf"):
                try:
                    file_hash = get_pdf_hash(file_path)
                    if file_hash == self.pdf_hash:
                        logger.debug(f"Found matching PDF by hash: {file_path}")
                        return file_path
                except Exception as e:
                    logger.debug(f"Error checking {file_path}: {e}")
                    continue

        logger.warning(f"No raw file found for PDF hash: {self.pdf_hash}")
        return None

    @property
    def file_url(self):
        """
        Returns the URL of the stored PDF file if available; otherwise, returns None.
        """
        try:
            return self.file.url if self.file and self.file.name else None
        except (ValueError, AttributeError):
            return None

    @property
    def anonymized_file_url(self):
        """
        Returns the URL of the stored PDF file if available; otherwise, returns None.
        """
        try:
            return self.anonymized_file.url if self.anonymized_file and self.anonymized_file.name else None
        except (ValueError, AttributeError):
            return None

    def __str__(self):
        """
        Return a string representation of the RawPdfFile, including its PDF hash, type, and center.
        """
        str_repr = f"{self.pdf_hash} ({self.pdf_type}, {self.center})"
        return str_repr

    def delete(self, *args, **kwargs):
        """
        Deletes the RawPdfFile instance from the database and removes the associated file from storage if it exists.

        This method ensures that the physical PDF file is deleted from the file system after the database record is removed. Logs warnings or errors if the file cannot be found or deleted.
        """
        primary_name = self.file.name if self.file and self.file.name else None
        anonymized_name = self.anonymized_file.name if self.anonymized_file and self.anonymized_file.name else None

        if delete_field_file(self.file, missing_ok=True, save=False):
            logger.info("Original file removed from storage: %s", primary_name)
        if delete_field_file(self.anonymized_file, missing_ok=True, save=False):
            logger.info("Anonymized file removed from storage: %s", anonymized_name)

        super().delete(*args, **kwargs)

    def validate_metadata_annotation(self, extracted_data_dict: Optional[dict] = None) -> bool:
        """
        Validate the metadata of the RawPdf instance.

        Called after annotation in the frontend, this method deletes the associated active file, updates the sensitive meta data with the user annotated data.
        It also ensures the video file is properly saved after the metadata update.
        """

        if not self.sensitive_meta:
            logger.error("No sensitive meta data associated with this PDF file.")
            return False

        if not extracted_data_dict:
            logger.error("No extracted data provided for validation.")
            return False

        if extracted_data_dict:
            self.sensitive_meta.update_from_dict(extracted_data_dict)
        else:
            return False

        # Save the sensitive meta to ensure changes are persisted
        self.sensitive_meta.save()

        # Save the RawPdfFile instance to ensure all changes are saved
        self.save()

        logger.info(f"Metadata for PDF {self.pk} validated and updated successfully.")

        deleted_original = delete_field_file(self.file, missing_ok=True, save=False)
        deleted_anonymized = delete_field_file(self.anonymized_file, missing_ok=True, save=False)

        if deleted_original or deleted_anonymized:
            self.save(update_fields=["file", "anonymized_file"])  # Persist cleared fields

        logger.info(f"Files for PDF {self.pk} deleted successfully.")
        return True

    @classmethod
    def create_from_file_initialized(
        cls,
        file_path: Path,
        center_name: str,
        delete_source: bool = True,
    ):
        """
        Creates a RawPdfFile instance from a file and center name, ensuring an associated RawPdfState exists.

        Parameters:
            file_path (Path): Path to the source PDF file.
            center_name (str): Name of the center to associate with the PDF.
            delete_source (bool): Whether to delete the source file after processing. Defaults to True.

        Returns:
            RawPdfFile: The created or retrieved RawPdfFile instance with an associated RawPdfState.
        """
        raw_pdf = cls.create_from_file(
            file_path=file_path,
            center_name=center_name,
            delete_source=delete_source,
        )
        _state = raw_pdf.get_or_create_state()

        return raw_pdf

    @classmethod
    def create_from_file(
        cls,
        file_path: Path,
        center_name,
        save=True,  # Parameter kept for compatibility, but save now happens internally
        delete_source=True,
    ):
        """
        Creates or retrieves a RawPdfFile instance from a given PDF file path and center name.

        If a RawPdfFile with the same PDF hash already exists, verifies the file exists in storage and restores it if missing. Otherwise, creates a new RawPdfFile, assigns the file, and saves it to storage. Optionally deletes the source file after processing.

        Parameters:
            file_path (Path): Path to the source PDF file.
            center_name (str): Name of the center to associate with the file.
            save (bool, optional): Deprecated; saving occurs internally.
            delete_source (bool, optional): Whether to delete the source file after processing (default True).

        Returns:
            RawPdfFile: The created or retrieved RawPdfFile instance.

        Raises:
            FileNotFoundError: If the source file does not exist.
            Center.DoesNotExist: If the specified center is not found.
            ValueError: If the PDF hash cannot be calculated.
            IOError: If the file fails to save to storage.
        """
        from endoreg_db.models.administration import Center

        if not file_path.exists():
            logger.error(f"Source file does not exist: {file_path}")
            raise FileNotFoundError(f"Source file not found: {file_path}")

        # 1. Calculate hash from source file
        try:
            pdf_hash = get_pdf_hash(file_path)
            logger.info(pdf_hash)
        except Exception as e:
            logger.error(f"Could not calculate hash for {file_path}: {e}")
            raise ValueError(f"Could not calculate hash for {file_path}") from e

        # 2. Check if record with this hash already exists
        existing_pdf_file = cls.objects.filter(pdf_hash=pdf_hash).first()
        if existing_pdf_file:
            logger.warning(
                "RawPdfFile with hash %s already exists (ID: %s)",
                pdf_hash,
                existing_pdf_file.pk,
            )

            # Verify physical file exists for the existing record
            try:
                if existing_pdf_file is not None and isinstance(existing_pdf_file, cls):
                    # Use storage API to check existence
                    _file = existing_pdf_file.file
                    assert _file is not None
                    if not _file.storage.exists(_file.name):
                        logger.warning(
                            "File for existing RawPdfFile %s not found in storage at %s. Attempting to restore from source %s",
                            pdf_hash,
                            _file.name,
                            file_path,
                        )
                        # Re-save the file from the source to potentially fix it
                        with file_path.open("rb") as f:
                            django_file = File(f, name=Path(_file.name).name)  # Use existing name if possible
                            existing_pdf_file.file = django_file
                            existing_pdf_file.save(update_fields=["file"])  # Only update file field
                    else:
                        pass
                        # logger.debug("File for existing RawPdfFile %s already exists in storage.", pdf_hash)
            except Exception as e:
                logger.error(
                    "Error verifying/restoring file for existing record %s: %s",
                    pdf_hash,
                    e,
                )

            # Delete the source temp file if requested
            if delete_source:
                try:
                    file_path.unlink()
                    # logger.info("Deleted source file %s after finding existing record.", file_path)
                except OSError as e:
                    logger.error("Error deleting source file %s: %s", file_path, e)

            return existing_pdf_file

        # --- Create new record if not existing ---
        assert center_name is not None, "center_name is required"
        try:
            center = Center.objects.get(name=center_name)
        except Center.DoesNotExist:
            logger.error(f"Center with name '{center_name}' not found.")
            raise

        # Generate a unique filename (e.g., using UUID)
        new_file_name, _uuid = get_uuid_filename(file_path)
        logger.info(f"Generated new filename: {new_file_name}")

        # Create model instance via manager so creation can be intercepted/mocked during tests
        try:
            with file_path.open("rb") as f:
                django_file = File(f, name=new_file_name)
                raw_pdf = cls.objects.create(
                    pdf_hash=pdf_hash,
                    center=center,
                    file=django_file,
                )

            _file = raw_pdf.file
            assert _file is not None
            logger.info(
                "Created and saved new RawPdfFile %s with file %s",
                raw_pdf.pk,
                _file.name,
            )

            if not _file.storage.exists(_file.name):
                logger.error(
                    "File was not saved correctly to storage path %s after model save.",
                    _file.name,
                )
                raise IOError(f"File not found at expected storage path after save: {_file.name}")

            try:
                logger.info("File saved to absolute path: %s", _file.path)
            except NotImplementedError:
                logger.info(
                    "File saved to storage path: %s (Absolute path not available from storage)",
                    _file.name,
                )

        except Exception as e:
            logger.error("Error processing or saving file %s for new record: %s", file_path, e)
            raise

        # Delete source file *after* successful save and verification
        if delete_source:
            try:
                file_path.unlink()
                logger.info("Deleted source file %s after creating new record.", file_path)
            except OSError as e:
                logger.error("Error deleting source file %s: %s", file_path, e)

        # raw_pdf.save() # unnecessary?
        return raw_pdf

    def save(self, *args, **kwargs):
        # Ensure hash is calculated before the first save if possible and not already set
        # This is primarily a fallback if instance created manually without using create_from_file
        """
        Saves the RawPdfFile instance, ensuring the PDF hash is set and related fields are derived from metadata.

        If the PDF hash is missing, attempts to calculate it from the file before saving. Validates that the file has a `.pdf` extension. If related fields such as patient, examination, center, or examiner are unset but available in the associated sensitive metadata, they are populated accordingly before saving.
        """
        if not self.pk and not self.pdf_hash and self.file:
            try:
                with ensure_local_file(self.file) as local_path:
                    self.pdf_hash = get_pdf_hash(local_path)
                    logger.info("Calculated hash during pre-save for %s", self.file.name)
            except Exception as exc:
                logger.warning(
                    "Could not calculate hash before initial save for %s: %s",
                    self.file.name,
                    exc,
                )

        if self.file and not self.file.name.endswith(".pdf"):
            raise ValidationError("Only PDF files are allowed")

        # If hash is still missing after potential creation logic (e.g., direct instantiation)
        # and the file exists in storage, try calculating it from storage path.
        # This is less ideal as it requires the file to be saved first.
        if not self.pdf_hash and self.pk and self.file and file_exists(self.file):
            try:
                with ensure_local_file(self.file) as local_path:
                    logger.warning("Hash missing for saved file %s. Recalculating.", self.file.name)
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
        Checks if the stored PDF file exists in storage and attempts to restore it from a fallback file path if missing.

        Parameters:
            fallback_file: Path or string representing the fallback file location to restore from if the stored file is missing.
        """
        if not isinstance(fallback_file, Path):
            fallback_file = Path(fallback_file)

        _file = self.file
        assert _file is not None
        try:
            if not _file.field.storage.exists(_file.name):
                logger.warning(f"File missing at storage path {_file.name}. Attempting copy from fallback {fallback_file}")
                if fallback_file.exists():
                    with fallback_file.open("rb") as f:
                        # Use save method which handles storage backend
                        _file.save(Path(_file.name).name, File(f), save=True)  # Re-save the file content
                    logger.info(f"Successfully restored file from fallback {fallback_file} to {_file.name}")
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
    def get_pdf_by_id(pdf_id: int) -> "RawPdfFile":
        try:
            return RawPdfFile.objects.get(pk=pdf_id)
        except RawPdfFile.DoesNotExist:
            raise ValueError(f"PDF with ID {pdf_id} does not exist.")

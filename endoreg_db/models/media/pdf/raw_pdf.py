from __future__ import annotations

# models/data_file/import_classes/raw_pdf.py
# django db model "RawPdf"
# Class to store raw pdf file using django file field
# Class contains classmethod to create object from pdf file
# objects contains methods to extract text, extract metadata from text and anonymize text from pdf file uzing agl_report_reader.ReportReader class
# ------------------------------------------------------------------------------
import uuid as uuid_lib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, TypedDict, Unpack, cast

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from lx_dtypes.models.contracts.pdf_file import PdfFileMetaJsonObject

from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from endoreg_db.schemas import validate_raw_pdf_meta_payload
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.encryption.encrypted import LazyEncryptedStorage
from endoreg_db.utils.paths import (
    ANONYM_REPORT_DIR,
    SENSITIVE_REPORT_DIR,
)
from endoreg_db.utils.storage_profile import (
    PayloadKind,
    StoragePolicy,
    resolve_storage_policy,
)

IMPORT_REPORT_DIR = path_utils.IMPORT_REPORT_DIR

if TYPE_CHECKING:
    from endoreg_db.models.administration.center.center import Center
    from endoreg_db.models.administration.person.examiner.examiner import Examiner
    from endoreg_db.models.administration.person.patient.patient import Patient
    from endoreg_db.models.media.pdf.report_file import AnonymExaminationReport
    from endoreg_db.models.medical.patient.patient_examination import (
        PatientExamination,
    )
    from endoreg_db.models.metadata.pdf_meta import PdfType
    from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
    from endoreg_db.models.state.raw_pdf import RawPdfState


class _RawPdfFileCreateKwargs(TypedDict, total=False):
    pass


class RawPdfFile(models.Model):
    objects = models.Manager["RawPdfFile"]()
    # Fields from AbstractPdfFile
    uuid: models.UUIDField[Any, Any] = models.UUIDField(
        default=uuid_lib.uuid4, unique=True, editable=False
    )
    pdf_hash: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)
    pdf_type: models.ForeignKey["PdfType | None"] = models.ForeignKey(
        "PdfType",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    center: models.ForeignKey["Center | None"] = models.ForeignKey(
        "Center",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    examination: models.ForeignKey["PatientExamination | None"] = models.ForeignKey(
        "PatientExamination",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="raw_pdf_files",
    )
    examiner: models.ForeignKey["Examiner | None"] = models.ForeignKey(
        "Examiner",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    text: models.TextField[Any, Any] = models.TextField(blank=True, null=True)
    date_created: models.DateTimeField[Any, Any] = models.DateTimeField(
        auto_now_add=True
    )
    date_modified: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

    file: models.FileField = models.FileField(
        # Use the relative path from the specific REPORT_DIR
        upload_to=SENSITIVE_REPORT_DIR.name,
        storage=LazyEncryptedStorage(),
        validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
    )
    processed_file: models.FileField = models.FileField(
        upload_to=ANONYM_REPORT_DIR.name,
        storage=LazyEncryptedStorage(),
        validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
        null=True,
        blank=True,
    )
    state: models.OneToOneField["RawPdfState | None"] = models.OneToOneField(
        "RawPdfState",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="raw_pdf_file",
    )
    patient: models.ForeignKey["Patient | None"] = models.ForeignKey(
        "Patient",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="raw_pdf_files",
    )
    sensitive_meta: models.ForeignKey["SensitiveMeta | None"] = models.ForeignKey(
        "SensitiveMeta",
        on_delete=models.SET_NULL,
        related_name="raw_pdf_files",
        null=True,
        blank=True,
    )
    state_report_processing_required: models.BooleanField[Any, Any] = (
        models.BooleanField(default=True)
    )
    state_report_processed: models.BooleanField[Any, Any] = models.BooleanField(
        default=False
    )
    raw_meta: models.JSONField[
        PdfFileMetaJsonObject | None, PdfFileMetaJsonObject | None
    ] = models.JSONField(blank=True, null=True)
    anonym_examination_report: models.OneToOneField[
        "AnonymExaminationReport | None"
    ] = models.OneToOneField(
        "AnonymExaminationReport",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="raw_pdf_file",
    )
    anonymized_text: models.TextField[Any, Any] = models.TextField(
        blank=True, null=True
    )

    class Meta:
        indexes = [
            models.Index(
                fields=["date_created"],
                name="raw_pdf_date_created_idx",
            ),
            models.Index(
                fields=["center", "date_created"],
                name="raw_pdf_center_time_idx",
            ),
        ]

    if TYPE_CHECKING:
        pk: int
        pdf_type_id: int | None
        center_id: int | None
        examination_id: int | None
        examiner_id: int | None
        state_id: int | None
        patient_id: int | None
        sensitive_meta_id: int | None
        anonym_examination_report_id: int | None

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
        from endoreg_db.services.raw_pdf_files import get_raw_pdf_plaintext_path

        return get_raw_pdf_plaintext_path(self)

    def set_file_path(self, file_path: Path) -> None:
        """
        Sets the file path of the stored report file.
        """
        from endoreg_db.services.raw_pdf_files import set_raw_pdf_file_path

        set_raw_pdf_file_path(self, file_path)

    @property
    def anonymized_file_path(self) -> Path | None:
        """
        Deprecated: return a local plaintext path only when one is explicitly available.

        Use ensure_local_file(self.processed_file) for tooling that requires a real path.
        """
        from endoreg_db.services.raw_pdf_files import get_processed_pdf_plaintext_path

        return get_processed_pdf_plaintext_path(self)

    def set_anonymized_file_path(self, file_path: Path) -> None:
        """
        Sets the file path of the anonymized report file.
        """
        from endoreg_db.services.raw_pdf_files import set_processed_pdf_file_path

        set_processed_pdf_file_path(self, file_path)

    def get_raw_file_path(self) -> Path | None:
        """
        Get the path to the raw report file, searching common locations.

        This method attempts to find the original raw report file by checking:
        1. Checking the file field if it already points to a valid file
        2. Direct hash-based path in import/report_import or sensitive_reports
        3. Scanning canonical report directories for files matching the hash

        Returns:
            Path to raw file if it exists, None otherwise
        """
        from endoreg_db.services.raw_pdf_files import get_raw_pdf_file_path

        return get_raw_pdf_file_path(self)

    @property
    def file_url(self) -> str | None:
        """
        Returns the URL of the stored report file if available; otherwise, returns None.
        """
        from endoreg_db.services.raw_pdf_files import get_raw_pdf_file_url

        return get_raw_pdf_file_url(self)

    @property
    def anonymized_file_url(self) -> str | None:
        """
        Returns the URL of the stored report file if available; otherwise, returns None.
        """
        from endoreg_db.services.raw_pdf_files import get_processed_pdf_file_url

        return get_processed_pdf_file_url(self)

    def __str__(self) -> str:
        """
        Return a string representation of the RawPdfFile, including its report hash, type, and center.
        """
        str_repr = f"{self.pdf_hash} ({self.pdf_type}, {self.center})"
        return str_repr

    def delete(
        self,
        using: str | None = None,
        keep_parents: bool = False,
    ) -> tuple[int, dict[str, int]]:
        """
        Deletes the RawPdfFile instance from the database and removes the associated file from storage if it exists.

        This method ensures that the physical report file is deleted from the file system after the database record is removed. Logs warnings or errors if the file cannot be found or deleted.
        """
        from endoreg_db.services.raw_pdf_files import delete_raw_pdf_with_owned_files

        delete_with_owned_files = cast(
            Callable[
                ["RawPdfFile", str | None, bool],
                tuple[int, dict[str, int]],
            ],
            delete_raw_pdf_with_owned_files,
        )
        return delete_with_owned_files(
            self,
            using,
            keep_parents,
        )

    # --- Convenience state/meta helpers used in tests and admin workflows ---

    def mark_sensitive_meta_processed(self, *, save: bool = True) -> "RawPdfFile":
        """
        Mark this video's processing state as having its sensitive meta fully processed.
        This proxies to the related VideoState and persists by default.
        """
        from endoreg_db.services.raw_pdf_files import (
            mark_report_sensitive_meta_processed,
        )

        return mark_report_sensitive_meta_processed(self, save=save)

    def mark_sensitive_meta_verified(self) -> "RawPdfFile":
        """
        Mark the associated SensitiveMeta as verified by setting both DOB and names as verified.
        Ensures the SensitiveMeta and its state exist.
        """
        from endoreg_db.services.raw_pdf_files import (
            mark_report_sensitive_meta_verified,
        )

        return mark_report_sensitive_meta_verified(self)

    def validate_metadata_annotation(
        self, extracted_data_dict: PdfFileMetaJsonObject | None = None
    ) -> bool:
        """
        Validate the metadata of the RawPdf instance.

        Called after annotation in the frontend, this method deletes the associated active file, updates the sensitive meta data with the user annotated data.
        It also ensures the video file is properly saved after the metadata update.
        """
        from endoreg_db.services.raw_pdf_files import (
            validate_report_metadata_annotation,
        )

        validate_annotation = cast(
            Callable[["RawPdfFile", PdfFileMetaJsonObject | None], bool],
            validate_report_metadata_annotation,
        )
        return validate_annotation(self, extracted_data_dict)

    @classmethod
    def create_from_file(
        cls,
        file_path: str | Path,
        center_name: str | None = None,
        **kwargs: Unpack[_RawPdfFileCreateKwargs],
    ) -> "RawPdfFile":
        """
        Creates or retrieves a RawPdfFile instance.
        """
        from endoreg_db.services.raw_pdf_files import create_raw_pdf_file_from_path

        return create_raw_pdf_file_from_path(
            file_path=file_path,
            center_name=center_name,
            model_cls=cls,
            **kwargs,
        )

    @classmethod
    def create_from_file_initialized(
        cls,
        file_path: str | Path,
        center_name: str | None = None,
        **kwargs: Unpack[_RawPdfFileCreateKwargs],
    ) -> "RawPdfFile":
        """
        Creates a RawPdfFile and immediately ensures states and metadata are initialized.
        """
        from endoreg_db.services.raw_pdf_files import (
            create_initialized_raw_pdf_file_from_path,
        )

        return create_initialized_raw_pdf_file_from_path(
            file_path=file_path,
            center_name=center_name,
            model_cls=cls,
            **kwargs,
        )

    def initialize(self) -> "RawPdfFile":
        """
        Initialize the RawPdfFile instance by ensuring related state exists and saving.
        Standardized to match VideoFile.initialize().
        """
        from endoreg_db.services.raw_pdf_files import initialize_raw_pdf_file

        return initialize_raw_pdf_file(self)

    def clean(self) -> None:
        super().clean()
        try:
            validated_raw_meta = validate_raw_pdf_meta_payload(self.raw_meta)
        except ValueError as exc:
            raise ValidationError({"raw_meta": str(exc)}) from exc
        self.raw_meta = cast(PdfFileMetaJsonObject | None, validated_raw_meta)

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        # Ensure hash is calculated before the first save if possible and not already set
        # This is primarily a fallback if instance created manually without using create_from_file
        """
        Saves the RawPdfFile instance, ensuring the report hash is set and related fields are derived from metadata.

        If the report hash is missing, attempts to calculate it from the file before saving. Validates that the file has a `.pdf` extension. If related fields such as patient, examination, center, or examiner are unset but available in the associated sensitive metadata, they are populated accordingly before saving.
        """
        from endoreg_db.services.raw_pdf_files import prepare_raw_pdf_before_save

        prepare_raw_pdf_before_save(self)
        self.clean()

        super().save(*args, **kwargs)

    def get_or_create_state(self) -> "RawPdfState":
        """
        Retrieve the associated RawPdfState for this RawPdfFile, creating and linking a new one if none exists.

        Returns:
            RawPdfState: The existing or newly created RawPdfState instance linked to this RawPdfFile.
        """
        from endoreg_db.services.raw_pdf_files import get_or_create_raw_pdf_state

        return get_or_create_raw_pdf_state(self)

    def verify_existing_file(self, fallback_file: Path | str) -> None:
        # This method might still be useful if called explicitly, but create_from_file now handles restoration
        # Ensure fallback_file is a Path object.
        """
        Checks if the stored report file exists in storage and attempts to restore it from a fallback file path if missing.

        Parameters:
            fallback_file: Path or string representing the fallback file location to restore from if the stored file is missing.
        """
        from endoreg_db.services.raw_pdf_files import verify_existing_raw_pdf_file

        verify_existing_raw_pdf_file(self, fallback_file)

    def process_file(
        self,
        text: str,
        anonymized_text: str,
        report_meta: PdfFileMetaJsonObject,
        verbose: bool,
    ) -> tuple[str, str, PdfFileMetaJsonObject]:
        from endoreg_db.services.raw_pdf_files import process_raw_pdf_file

        process_report = cast(
            Callable[
                ["RawPdfFile", str, str, PdfFileMetaJsonObject, bool],
                tuple[str, str, PdfFileMetaJsonObject],
            ],
            process_raw_pdf_file,
        )
        return process_report(
            self,
            text,
            anonymized_text,
            report_meta,
            verbose,
        )

    def get_report_reader_config(self) -> PdfFileMetaJsonObject:
        from endoreg_db.services.raw_pdf_files import build_report_reader_config

        return build_report_reader_config(self)

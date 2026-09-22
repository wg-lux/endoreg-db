from __future__ import annotations

import uuid as uuid_lib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, TypedDict, Unpack, cast

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from endoreg_db.utils.storage.report_fields import ReportArtifactFieldFile
from lx_dtypes.models.contracts.pdf_file import PdfFileMetaJsonObject

from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from endoreg_db.schemas import validate_raw_pdf_meta_payload
from endoreg_db.utils.encryption.encrypted import LazyEncryptedStorage
from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.utils.storage_profile import (
    PayloadKind,
    StoragePolicy,
    resolve_storage_policy,
)

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

    if TYPE_CHECKING:
        file: ReportArtifactFieldFile
        processed_file: ReportArtifactFieldFile
    else:
        file: models.FileField = models.FileField(
            upload_to=get_runtime_paths().sensitive_report.name,
            storage=LazyEncryptedStorage(),
            validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
        )
        processed_file: models.FileField = models.FileField(
            upload_to=get_runtime_paths().anonym_report.name,
            storage=LazyEncryptedStorage(),
            validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
            null=True,
            blank=True,
        )
        file.attr_class = ReportArtifactFieldFile
        processed_file.attr_class = ReportArtifactFieldFile

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
        return self.file.local_plaintext_path()

    def set_file_path(self, file_path: Path | str) -> None:
        from endoreg_db.services.raw_pdf_files import set_raw_pdf_file_path

        set_raw_pdf_file_path(self, Path(file_path), save=False)

    @property
    def anonymized_file_path(self) -> Path | None:
        return self.processed_file.local_plaintext_path()

    def set_anonymized_file_path(self, file_path: Path | str) -> None:
        from endoreg_db.services.raw_pdf_files import set_processed_pdf_file_path

        set_processed_pdf_file_path(self, Path(file_path), save=False)

    def get_raw_file_path(self) -> Path | None:
        return self.file_path

    @property
    def file_url(self) -> str | None:
        from endoreg_db.services.raw_pdf_files import get_raw_pdf_file_url

        return get_raw_pdf_file_url(self)

    @property
    def anonymized_file_url(self) -> str | None:
        from endoreg_db.services.raw_pdf_files import get_processed_pdf_file_url

        return get_processed_pdf_file_url(self)

    def __str__(self) -> str:
        """
        Return a string representation of the RawPdfFile, including its report hash, type, and center.
        """
        return f"{self.pdf_hash} ({self.pdf_type}, {self.center})"

    def delete(
        self,
        using: str | None = None,
        keep_parents: bool = False,
    ) -> tuple[int, dict[str, int]]:
        from endoreg_db.services.raw_pdf_files import delete_raw_pdf_with_owned_files

        return delete_raw_pdf_with_owned_files(
            self, using=using, keep_parents=keep_parents
        )

    # --- Convenience state/meta helpers used in tests and admin workflows ---

    def mark_sensitive_meta_processed(self, *, save: bool = True) -> "RawPdfFile":
        from endoreg_db.services.raw_pdf_files import (
            mark_report_sensitive_meta_processed,
        )

        return mark_report_sensitive_meta_processed(self, save=save)

    def mark_sensitive_meta_verified(self) -> "RawPdfFile":
        from endoreg_db.services.raw_pdf_files import (
            mark_report_sensitive_meta_verified,
        )

        return mark_report_sensitive_meta_verified(self)

    def validate_metadata_annotation(
        self, extracted_data_dict: PdfFileMetaJsonObject | None = None
    ) -> bool:
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
        from endoreg_db.services.raw_pdf_files import prepare_raw_pdf_before_save

        prepare_raw_pdf_before_save(self)
        self.clean()

        super().save(*args, **kwargs)

    def get_or_create_state(self) -> "RawPdfState":
        from endoreg_db.services.raw_pdf_files import get_or_create_raw_pdf_state

        return get_or_create_raw_pdf_state(self)

    def verify_existing_file(self, fallback_file: Path | str) -> None:
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

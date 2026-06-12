# pyright: reportUnusedClass=false
from __future__ import annotations

import re
from pathlib import Path
from datetime import datetime
from typing import Protocol, TypedDict, cast

from rest_framework import serializers

from lx_dtypes.models.contracts import DocumentType as DocumentTypeContract

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.anonymization import AnonymizationState

PATH_PATTERN = re.compile(r"([A-Za-z]:\\[^\s]+|/[^\s]+)")
DOCUMENT_TYPE_VALUES = {document_type.value for document_type in DocumentTypeContract}


class _FileOverviewUploadJobSummary(TypedDict, total=False):
    id: str
    status: str
    ingest_mode: str
    source_system: str
    source_center_key: str | None
    original_filename: str
    source_file_persisted: bool
    cleanup_status: str
    created_at: str | None
    updated_at: str | None
    error_detail: str


class _FileOverviewPayload(TypedDict):
    id: int | str | None
    filename: str
    media_type: str
    anonymization_status: AnonymizationState
    annotation_status: str
    created_at: datetime
    sensitive_meta_id: int | None
    file_size: int
    upload_job: _FileOverviewUploadJobSummary | None
    document_type: str | None
    patient_hash_display: str | None
    examination_hash_display: str | None
    pseudo_patient_id: int | None
    pseudo_examination_id: int | None


class _FileOverviewInstanceLike(Protocol):
    pk: int | str | None
    state: object | None
    sensitive_meta: object | None


class _FileOverviewVideoLike(_FileOverviewInstanceLike, Protocol):
    raw_file: object | None
    original_file_name: str | None
    uploaded_at: datetime


class _FileOverviewPdfLike(_FileOverviewInstanceLike, Protocol):
    file: object | None
    date_created: datetime


class _FileOverviewUploadJobLike(Protocol):
    id: int | str
    status: str
    ingest_mode: str
    source_system: str
    source_center: object | None
    original_filename: str | None
    source_file_persisted: bool
    cleanup_status: str
    created_at: datetime | None
    updated_at: datetime | None
    error_detail: str | None


class _FileOverviewSensitiveMetaLike(Protocol):
    pk: int | None
    patient_hash: str | None
    examination_hash: str | None
    pseudo_patient_id: int | None
    pseudo_examination_id: int | None


class FileOverviewSerializer(serializers.Serializer[_FileOverviewPayload]):
    id = serializers.IntegerField(read_only=True)
    filename = serializers.CharField(read_only=True)
    media_type = serializers.CharField(read_only=True)
    anonymization_status = serializers.CharField(read_only=True)
    annotation_status = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    sensitive_meta_id = serializers.IntegerField(read_only=True, allow_null=True)
    file_size = serializers.IntegerField(read_only=True, required=False)
    upload_job = serializers.DictField(read_only=True, allow_null=True, required=False)
    document_type = serializers.CharField(
        read_only=True, allow_null=True, required=False
    )
    patient_hash_display = serializers.CharField(
        read_only=True, allow_null=True, required=False
    )
    examination_hash_display = serializers.CharField(
        read_only=True, allow_null=True, required=False
    )
    pseudo_patient_id = serializers.IntegerField(
        read_only=True, allow_null=True, required=False
    )
    pseudo_examination_id = serializers.IntegerField(
        read_only=True, allow_null=True, required=False
    )

    def _datetime_value(self, value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    def _safe_original_filename(self, upload_job: _FileOverviewUploadJobLike) -> str:
        if not upload_job.original_filename:
            return ""
        normalized_name = str(upload_job.original_filename).replace("\\", "/")
        return Path(normalized_name).name

    def _safe_error_detail(self, upload_job: _FileOverviewUploadJobLike) -> str:
        if upload_job.status not in {UploadJob.Status.ERROR, UploadJob.Status.LOST}:
            return ""

        detail = " ".join(str(upload_job.error_detail or "").split())
        detail = PATH_PATTERN.sub("[path]", detail)
        if len(detail) > 240:
            return f"{detail[:237]}..."
        return detail

    def _upload_job_summary(
        self, instance: object
    ) -> _FileOverviewUploadJobSummary | None:
        upload_job = cast(
            _FileOverviewUploadJobLike | None,
            getattr(instance, "_overview_upload_job", None),
        )
        if upload_job is None:
            return None

        source_center = getattr(upload_job, "source_center", None)
        summary: _FileOverviewUploadJobSummary = {
            "id": str(upload_job.id),
            "status": upload_job.status,
            "ingest_mode": upload_job.ingest_mode,
            "source_system": upload_job.source_system,
            "source_center_key": (
                source_center.center_key if source_center is not None else None
            ),
            "original_filename": self._safe_original_filename(upload_job),
            "source_file_persisted": upload_job.source_file_persisted,
            "cleanup_status": upload_job.cleanup_status,
            "created_at": self._datetime_value(upload_job.created_at),
            "updated_at": self._datetime_value(upload_job.updated_at),
        }

        error_detail = self._safe_error_detail(upload_job)
        if error_detail:
            summary["error_detail"] = error_detail

        return summary

    def _hash_display(self, value: str | None) -> str | None:
        return f"...{value[-8:]}" if value else None

    def _pdf_document_type(self, instance: RawPdfFile) -> str | None:
        report = getattr(instance, "anonym_examination_report", None)
        report_type = getattr(report, "type", None) if report is not None else None
        report_type_name = getattr(report_type, "name", None)
        if (
            isinstance(report_type_name, str)
            and report_type_name in DOCUMENT_TYPE_VALUES
        ):
            return report_type_name

        raw_meta = instance.raw_meta if isinstance(instance.raw_meta, dict) else {}
        raw_document_type = raw_meta.get("document_type")
        if (
            isinstance(raw_document_type, str)
            and raw_document_type in DOCUMENT_TYPE_VALUES
        ):
            return raw_document_type

        return None

    def to_representation(self, instance: object) -> dict[str, object]:
        # 1. Extract Type-Specific Data
        if isinstance(instance, VideoFile):
            media_type = "video"
            raw_file_name = getattr(instance.raw_file, "name", "") or ""
            filename = instance.original_file_name or (
                raw_file_name.split("/")[-1] if raw_file_name else "unknown_video"
            )
            created_at = instance.uploaded_at
            # Use the state relation optimized in the View
            state_obj = cast(object | None, instance.state)
            sensitive_meta = cast(
                _FileOverviewSensitiveMetaLike | None, instance.sensitive_meta
            )
            document_type = None
            try:
                file_size = instance.raw_file.size if instance.raw_file else 0
            except Exception:
                file_size = 0

        elif isinstance(instance, RawPdfFile):
            media_type = "pdf"
            report_file_name = getattr(instance.file, "name", "") or ""
            filename = (
                report_file_name.split("/")[-1]
                if report_file_name
                else "unknown_report"
            )
            created_at = instance.date_created
            state_obj = cast(object | None, instance.state)
            sensitive_meta = cast(
                _FileOverviewSensitiveMetaLike | None, instance.sensitive_meta
            )
            document_type = self._pdf_document_type(instance)
            try:
                file_size = instance.file.size if instance.file else 0
            except Exception:
                file_size = 0

        else:
            raise TypeError(f"Unexpected object type: {type(instance)}")

        # 2. Determine Status (Single Source of Truth: The State Model)
        # This uses the @property .anonymization_status from VideoState/RawPdfState
        raw_status = getattr(
            state_obj, "anonymization_status", AnonymizationState.NOT_STARTED
        )

        # 3. Map to frontend annotation_status
        annot_status = "not_started"

        # FIX: Explicitly check against the Enum value
        if raw_status == AnonymizationState.VALIDATED:
            annot_status = "validated"

        # 4. Return Payload
        return {
            "id": instance.pk,
            "filename": filename,
            "media_type": media_type,
            "anonymization_status": raw_status,
            "annotation_status": annot_status,
            "created_at": created_at,
            "sensitive_meta_id": sensitive_meta.pk if sensitive_meta else None,
            "file_size": file_size,
            "upload_job": self._upload_job_summary(instance),
            "document_type": document_type,
            "patient_hash_display": (
                self._hash_display(sensitive_meta.patient_hash)
                if sensitive_meta
                else None
            ),
            "examination_hash_display": (
                self._hash_display(sensitive_meta.examination_hash)
                if sensitive_meta
                else None
            ),
            "pseudo_patient_id": (
                getattr(sensitive_meta, "pseudo_patient_id", None)
                if sensitive_meta
                else None
            ),
            "pseudo_examination_id": (
                getattr(sensitive_meta, "pseudo_examination_id", None)
                if sensitive_meta
                else None
            ),
        }

# pyright: reportUnusedClass=false
from __future__ import annotations

from pathlib import Path
from datetime import datetime
from typing import Iterable, Protocol, TypedDict, cast

from rest_framework import serializers

from lx_dtypes.models.contracts import DocumentType as DocumentTypeContract
from lx_dtypes.models.contracts.anonymization_overview import (
    OverviewHlsMaterializationData,
    OverviewHlsMaterializationPayload,
    OverviewUploadJobMonitoringData,
    OverviewUploadJobMonitoringPayload,
)
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.models.state.anonymization import AnonymizationState
from endoreg_db.services.hub.import_monitoring import safe_import_error_detail

DOCUMENT_TYPE_VALUES = {document_type.value for document_type in DocumentTypeContract}


def safe_upload_job_original_filename(upload_job: _FileOverviewUploadJobLike) -> str:
    if not upload_job.original_filename:
        return ""
    normalized_name = str(upload_job.original_filename).replace("\\", "/")
    return Path(normalized_name).name


def overview_upload_job_summary(
    upload_job: _FileOverviewUploadJobLike,
) -> OverviewUploadJobMonitoringData:
    source_center = getattr(upload_job, "source_center", None)
    if upload_job.status == "anonymized":
        allowed_actions = ["delete"]
    elif (
        upload_job.status in {"error", "lost"}
        and upload_job.error_code != "duplicate_content"
    ):
        allowed_actions = ["safe_reimport", "delete"]
    else:
        allowed_actions = []
    return OverviewUploadJobMonitoringPayload.model_validate(
        {
            "id": upload_job.id,
            "status": upload_job.status,
            "ingest_mode": upload_job.ingest_mode,
            "source_system": upload_job.source_system or "unknown",
            "source_center_key": (
                source_center.center_key if source_center is not None else None
            ),
            "original_filename": safe_upload_job_original_filename(upload_job),
            "source_file_persisted": upload_job.source_file_persisted,
            "cleanup_status": upload_job.cleanup_status,
            "allowed_actions": allowed_actions,
            "error_code": upload_job.error_code,
            "error_detail": safe_import_error_detail(upload_job.error_code),
            "retryable": upload_job.retryable,
            "retry_count": upload_job.retry_count,
            "max_retries": upload_job.max_retries,
            "next_retry_at": upload_job.next_retry_at,
            "last_attempt_at": upload_job.last_attempt_at,
            "created_at": upload_job.created_at,
            "updated_at": upload_job.updated_at,
        }
    ).to_data()


class _FileOverviewPayload(TypedDict):
    id: int | str | None
    filename: str
    media_type: str
    anonymization_status: AnonymizationState
    annotation_status: str
    created_at: datetime
    sensitive_meta_id: int | None
    file_size: int
    upload_job: OverviewUploadJobMonitoringData | None
    hls_materializations: list[OverviewHlsMaterializationData]
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
    created_at: datetime
    updated_at: datetime
    error_detail: str | None
    error_code: str
    retryable: bool
    retry_count: int
    max_retries: int
    next_retry_at: datetime | None
    last_attempt_at: datetime | None


class _FileOverviewSensitiveMetaLike(Protocol):
    pk: int | None
    patient_hash: str | None
    examination_hash: str | None
    pseudo_patient_id: int | None
    pseudo_examination_id: int | None


class _HlsArtifactManager(Protocol):
    def all(self) -> Iterable[VideoHlsArtifact]: ...


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
    hls_materializations = serializers.ListField(read_only=True, required=False)
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

    def _safe_original_filename(self, upload_job: _FileOverviewUploadJobLike) -> str:
        return safe_upload_job_original_filename(upload_job)

    def _overview_upload_job(
        self, instance: object
    ) -> _FileOverviewUploadJobLike | None:
        return cast(
            _FileOverviewUploadJobLike | None,
            getattr(instance, "_overview_upload_job", None),
        )

    def _display_filename(self, instance: object, fallback: str) -> str:
        upload_job = self._overview_upload_job(instance)
        if upload_job is not None:
            original_filename = self._safe_original_filename(upload_job)
            if original_filename:
                return original_filename
        return fallback

    def _upload_job_summary(
        self, instance: object
    ) -> OverviewUploadJobMonitoringData | None:
        upload_job = self._overview_upload_job(instance)
        if upload_job is None:
            return None
        return overview_upload_job_summary(upload_job)

    def _hls_materializations(
        self, instance: object
    ) -> list[OverviewHlsMaterializationData]:
        if not isinstance(instance, VideoFile):
            return []
        manager = cast(_HlsArtifactManager, getattr(instance, "hls_artifacts"))
        artifacts = list(manager.all())
        upload_job = self._overview_upload_job(instance)
        return [
            OverviewHlsMaterializationPayload.model_validate(
                {
                    "artifact_kind": artifact.artifact_kind,
                    "status": artifact.status,
                    "triggering_upload_job_id": (
                        upload_job.id if upload_job is not None else None
                    ),
                    "source_generation_id": artifact.source_generation_id,
                    "target_generation_id": artifact.key_id,
                    "segment_count": artifact.segment_count,
                    "error_code": artifact.error_code,
                    "created_at": artifact.created_at,
                    "updated_at": artifact.updated_at,
                }
            ).to_data()
            for artifact in sorted(artifacts, key=lambda item: item.artifact_kind)
        ]

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

        raw_meta: JsonObject
        if isinstance(instance.raw_meta, dict):
            raw_meta = cast(JsonObject, instance.raw_meta)
        else:
            raw_meta = cast(JsonObject, {})
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
            fallback_filename = instance.original_file_name or (
                raw_file_name.split("/")[-1] if raw_file_name else "unknown_video"
            )
            filename = self._display_filename(instance, fallback_filename)
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
            fallback_filename = (
                report_file_name.split("/")[-1]
                if report_file_name
                else "unknown_report"
            )
            filename = self._display_filename(instance, fallback_filename)
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
            "hls_materializations": self._hls_materializations(instance),
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


class CrossCenterProcessedOverviewSerializer(serializers.Serializer[VideoFile]):
    """Shape-compatible, pseudonymous overview payload for hub-wide reads."""

    def to_representation(self, instance: VideoFile) -> dict[str, object]:
        state = getattr(instance, "state", None)
        raw_status = getattr(
            state,
            "anonymization_status",
            AnonymizationState.NOT_STARTED,
        )
        center = getattr(instance, "center", None)
        return {
            "id": instance.pk,
            "filename": f"Video {instance.pk}",
            "media_type": "video",
            "anonymization_status": raw_status,
            "annotation_status": (
                "validated"
                if raw_status == AnonymizationState.VALIDATED
                else "not_started"
            ),
            "created_at": instance.uploaded_at,
            "sensitive_meta_id": None,
            "file_size": 0,
            "upload_job": None,
            "hls_materializations": [],
            "document_type": None,
            "patient_hash_display": None,
            "examination_hash_display": None,
            "pseudo_patient_id": None,
            "pseudo_examination_id": None,
            "center_key": getattr(center, "center_key", None),
            "center_name": getattr(center, "display_name", None),
        }

import re
from pathlib import Path
from typing import TYPE_CHECKING

from rest_framework import serializers

from lx_dtypes.models.contracts import DocumentType as DocumentTypeContract

from endoreg_db.models import UploadJob
from endoreg_db.models.media import RawPdfFile, VideoFile
from endoreg_db.models.state.anonymization import AnonymizationState

if TYPE_CHECKING:
    pass

PATH_PATTERN = re.compile(r"([A-Za-z]:\\[^\s]+|/[^\s]+)")
DOCUMENT_TYPE_VALUES = {document_type.value for document_type in DocumentTypeContract}


class FileOverviewSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    filename = serializers.CharField(read_only=True)
    mediaType = serializers.CharField(read_only=True)
    anonymizationStatus = serializers.CharField(read_only=True)
    annotationStatus = serializers.CharField(read_only=True)
    createdAt = serializers.DateTimeField(read_only=True)
    sensitiveMetaId = serializers.IntegerField(read_only=True, allow_null=True)
    fileSize = serializers.IntegerField(read_only=True, required=False)
    uploadJob = serializers.DictField(read_only=True, allow_null=True, required=False)
    documentType = serializers.CharField(
        read_only=True, allow_null=True, required=False
    )
    patientHashDisplay = serializers.CharField(
        read_only=True, allow_null=True, required=False
    )
    examinationHashDisplay = serializers.CharField(
        read_only=True, allow_null=True, required=False
    )
    pseudoPatientId = serializers.IntegerField(
        read_only=True, allow_null=True, required=False
    )
    pseudoExaminationId = serializers.IntegerField(
        read_only=True, allow_null=True, required=False
    )

    def _datetime_value(self, value):
        return value.isoformat() if value else None

    def _safe_original_filename(self, upload_job: UploadJob) -> str:
        if not upload_job.original_filename:
            return ""
        normalized_name = str(upload_job.original_filename).replace("\\", "/")
        return Path(normalized_name).name

    def _safe_error_detail(self, upload_job: UploadJob) -> str:
        if upload_job.status not in {UploadJob.Status.ERROR, UploadJob.Status.LOST}:
            return ""

        detail = " ".join(str(upload_job.error_detail or "").split())
        detail = PATH_PATTERN.sub("[path]", detail)
        if len(detail) > 240:
            return f"{detail[:237]}..."
        return detail

    def _upload_job_summary(self, instance):
        upload_job = getattr(instance, "_overview_upload_job", None)
        if upload_job is None:
            return None

        source_center = getattr(upload_job, "source_center", None)
        summary = {
            "id": str(upload_job.id),
            "status": upload_job.status,
            "ingestMode": upload_job.ingest_mode,
            "sourceSystem": upload_job.source_system,
            "sourceCenterKey": (
                source_center.center_key if source_center is not None else None
            ),
            "originalFilename": self._safe_original_filename(upload_job),
            "sourceFilePersisted": upload_job.source_file_persisted,
            "cleanupStatus": upload_job.cleanup_status,
            "createdAt": self._datetime_value(upload_job.created_at),
            "updatedAt": self._datetime_value(upload_job.updated_at),
        }

        error_detail = self._safe_error_detail(upload_job)
        if error_detail:
            summary["errorDetail"] = error_detail

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

    def to_representation(self, instance):
        # 1. Extract Type-Specific Data
        if isinstance(instance, VideoFile):
            media_type = "video"
            filename = instance.original_file_name or (
                instance.raw_file.name.split("/")[-1]
                if instance.raw_file
                else "unknown_video"
            )
            created_at = instance.uploaded_at
            # Use the state relation optimized in the View
            state_obj = instance.state
            sensitive_meta = instance.sensitive_meta
            document_type = None
            try:
                file_size = instance.raw_file.size if instance.raw_file else 0
            except Exception:
                file_size = 0

        elif isinstance(instance, RawPdfFile):
            media_type = "pdf"
            filename = (
                instance.file.name.split("/")[-1] if instance.file else "unknown_report"
            )
            created_at = instance.date_created
            state_obj = instance.state
            sensitive_meta = instance.sensitive_meta
            document_type = self._pdf_document_type(instance)
            try:
                file_size = instance.file.size if instance.file else 0
            except Exception:
                file_size = 0

        else:
            raise TypeError(f"Unexpected object type: {type(instance)}")

        # 2. Determine Status (Single Source of Truth: The State Model)
        # This uses the @property .anonymization_status from VideoState/RawPdfState
        raw_status = (
            state_obj.anonymization_status
            if state_obj
            else AnonymizationState.NOT_STARTED
        )

        # 3. Map to Frontend 'annotationStatus'
        annot_status = "not_started"

        # FIX: Explicitly check against the Enum value
        if raw_status == AnonymizationState.VALIDATED:
            annot_status = "validated"

        # 4. Return Payload
        return {
            "id": instance.pk,
            "filename": filename,
            "mediaType": media_type,
            "anonymizationStatus": raw_status,
            "annotationStatus": annot_status,
            "createdAt": created_at,
            "sensitiveMetaId": sensitive_meta.pk if sensitive_meta else None,
            "fileSize": file_size,
            "uploadJob": self._upload_job_summary(instance),
            "documentType": document_type,
            "patientHashDisplay": (
                self._hash_display(sensitive_meta.patient_hash)
                if sensitive_meta
                else None
            ),
            "examinationHashDisplay": (
                self._hash_display(sensitive_meta.examination_hash)
                if sensitive_meta
                else None
            ),
            "pseudoPatientId": (
                sensitive_meta.pseudo_patient_id if sensitive_meta else None
            ),
            "pseudoExaminationId": (
                sensitive_meta.pseudo_examination_id if sensitive_meta else None
            ),
        }

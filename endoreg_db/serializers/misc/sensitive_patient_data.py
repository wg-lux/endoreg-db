from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, cast

from rest_framework import serializers

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.utils.media_urls import build_pdf_stream_path
from lx_dtypes.models.contracts.sensitive_patient_data import (
    SensitiveMetaReportData,
)


class _CenterLike(Protocol):
    name: str


class _TagRelationLike(Protocol):
    def order_by(self, *fields: str) -> "_TagRelationLike": ...

    def values_list(self, field_name: str, flat: bool) -> list[str]: ...


class _SensitiveMetaLike(Protocol):
    pk: int | None
    patient_first_name: str | None
    patient_last_name: str | None
    patient_dob: datetime | date | None
    examination_date: datetime | date | None
    center: _CenterLike | None
    endoscope_type: str | None
    endoscope_sn: str | None
    patient_gender: object | None
    validation_comment: str | None
    tags: _TagRelationLike


class VoPPatientDataSerializer(serializers.Serializer[VideoFile | RawPdfFile]):
    id = serializers.IntegerField(read_only=True)
    sensitive_meta_id = serializers.IntegerField(read_only=True)
    text = serializers.CharField(read_only=True)
    anonymized_text = serializers.CharField(read_only=True)
    report_meta = serializers.JSONField(read_only=True)
    status = serializers.CharField(read_only=True)
    error = serializers.BooleanField(read_only=True)

    def to_representation(self, instance: VideoFile | RawPdfFile) -> dict[str, object]:
        if isinstance(instance, VideoFile):
            return self._video_representation(instance)
        return self._pdf_representation(instance)

    def _video_representation(self, instance: VideoFile) -> dict[str, object]:
        sensitive_meta = cast(_SensitiveMetaLike | None, instance.sensitive_meta)
        text = ""
        anonym_text = ""
        if sensitive_meta is not None:
            text_parts: list[str] = []
            if sensitive_meta.patient_first_name or sensitive_meta.patient_last_name:
                patient_name = (
                    f"{sensitive_meta.patient_first_name or ''} "
                    f"{sensitive_meta.patient_last_name or ''}"
                ).strip()
                text_parts.append(f"Patient: {patient_name}")
            if sensitive_meta.patient_dob is not None:
                patient_dob = sensitive_meta.patient_dob
                text_parts.append(
                    f"Date of Birth: {patient_dob.date() if isinstance(patient_dob, datetime) else patient_dob}"
                )
            if sensitive_meta.examination_date is not None:
                examination_date = sensitive_meta.examination_date
                text_parts.append(
                    f"Examination Date: {examination_date.date() if isinstance(examination_date, datetime) else examination_date}"
                )
            if sensitive_meta.center is not None:
                text_parts.append(f"Center: {sensitive_meta.center.name}")
            text = "\n".join(text_parts)
            anonym_text = text
            if sensitive_meta.patient_first_name:
                anonym_text = anonym_text.replace(
                    sensitive_meta.patient_first_name, "[FIRST_NAME]"
                )
            if sensitive_meta.patient_last_name:
                anonym_text = anonym_text.replace(
                    sensitive_meta.patient_last_name, "[LAST_NAME]"
                )
            if sensitive_meta.patient_dob is not None:
                patient_dob = sensitive_meta.patient_dob
                dob_text = (
                    patient_dob.date().isoformat()
                    if isinstance(patient_dob, datetime)
                    else patient_dob.isoformat()
                )
                anonym_text = anonym_text.replace(dob_text, "[DOB]")

        report_meta = self._serialize_sensitive_meta(sensitive_meta)
        return {
            "id": instance.pk,
            "sensitive_meta_id": sensitive_meta.pk
            if sensitive_meta is not None
            else None,
            "text": text,
            "anonymized_text": anonym_text,
            "report_meta": report_meta,
            "status": "processing"
            if instance.state is not None
            and bool(getattr(instance.state, "frames_extracted", False))
            else "not_started",
            "error": False,
        }

    def _pdf_representation(self, instance: RawPdfFile) -> dict[str, object]:
        sensitive_meta = cast(_SensitiveMetaLike | None, instance.sensitive_meta)
        report_meta = self._serialize_sensitive_meta(sensitive_meta)
        return {
            "id": instance.pk,
            "sensitive_meta_id": sensitive_meta.pk
            if sensitive_meta is not None
            else None,
            "text": instance.text or "",
            "anonymized_text": instance.anonymized_text or "",
            "report_meta": report_meta,
            "status": "done" if instance.anonymized_text else "not_started",
            "error": False,
            "pdf_stream_url": build_pdf_stream_path(instance.pk),
        }

    def _serialize_sensitive_meta(
        self, sensitive_meta: _SensitiveMetaLike | None
    ) -> SensitiveMetaReportData | None:
        if sensitive_meta is None:
            return None
        return {
            "id": sensitive_meta.pk,
            "patient_first_name": sensitive_meta.patient_first_name or "",
            "patient_last_name": sensitive_meta.patient_last_name or "",
            "patient_dob": (
                sensitive_meta.patient_dob.date().isoformat()
                if isinstance(sensitive_meta.patient_dob, datetime)
                else sensitive_meta.patient_dob.isoformat()
                if sensitive_meta.patient_dob is not None
                else ""
            ),
            "patient_gender": str(sensitive_meta.patient_gender)
            if sensitive_meta.patient_gender is not None
            else "",
            "examination_date": (
                sensitive_meta.examination_date.date().isoformat()
                if isinstance(sensitive_meta.examination_date, datetime)
                else sensitive_meta.examination_date.isoformat()
                if sensitive_meta.examination_date is not None
                else ""
            ),
            "center_name": sensitive_meta.center.name if sensitive_meta.center else "",
            "endoscope_type": sensitive_meta.endoscope_type or "",
            "endoscope_sn": sensitive_meta.endoscope_sn or "",
            "is_verified": bool(getattr(sensitive_meta, "is_verified", False)),
            "tags": list(
                sensitive_meta.tags.order_by("name").values_list("name", flat=True)
            ),
            "validation_comment": sensitive_meta.validation_comment or "",
        }

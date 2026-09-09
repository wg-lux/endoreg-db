# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
from datetime import date, datetime
from typing import Protocol, cast
from pathlib import Path

from django.conf import settings
from rest_framework import serializers

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.video_segment_validation_workflow import (
    post_validation_rebuild_summary,
    resolve_segment_annotation_status,
    segment_annotations_are_final,
)
from endoreg_db.serializers.video.video_file_brief import VideoBriefSerializer
from ...utils.calc_duration_seconds import _calc_duration_vf


class _VideoStateLike(Protocol):
    outside_segments_removed: bool
    anonymization_status: object
    processing_error: bool


class _SensitiveMetaLike(Protocol):
    patient_first_name: str | None
    patient_last_name: str | None
    patient_dob: date | datetime | None
    examination_date: date | datetime | None


class _VideoDetailLike(Protocol):
    processed_file: object
    raw_file: object
    duration: float | None
    sensitive_meta: _SensitiveMetaLike | None
    state: _VideoStateLike | None
    meta: dict[str, object] | None


class VideoDetailSerializer(VideoBriefSerializer):
    # pull selected fields from SensitiveMeta (READ-ONLY) - using SerializerMethodField to handle datetime->date conversion
    patient_first_name = serializers.CharField(
        source="sensitive_meta.patient_first_name", read_only=True
    )
    patient_last_name = serializers.CharField(
        source="sensitive_meta.patient_last_name", read_only=True
    )
    patient_dob = serializers.SerializerMethodField()
    examination_date = serializers.SerializerMethodField()

    file = serializers.SerializerMethodField()
    full_path = serializers.SerializerMethodField()
    duration = serializers.SerializerMethodField()
    segment_annotations_validated = serializers.SerializerMethodField()
    segment_annotation_status = serializers.SerializerMethodField()
    outside_segments_removed = serializers.SerializerMethodField()
    post_validation_rebuild = serializers.SerializerMethodField()
    anonymization_status = serializers.SerializerMethodField()
    integrity_status = serializers.SerializerMethodField()
    integrity_error = serializers.SerializerMethodField()

    class Meta(VideoBriefSerializer.Meta):
        fields = list(VideoBriefSerializer.Meta.fields) + [
            "file",
            "full_path",
            "patient_first_name",
            "patient_last_name",
            "patient_dob",
            "examination_date",
            "duration",
            "export_segments_by_video",
            "segment_annotations_validated",
            "segment_annotation_status",
            "outside_segments_removed",
            "post_validation_rebuild",
            "anonymization_status",
            "integrity_status",
            "integrity_error",
        ]

    # ---------- helpers ---------- #
    def get_file(self, obj: _VideoDetailLike) -> str | None:
        f = obj.processed_file or obj.raw_file
        return cast(str, getattr(f, "name", None)) if f else None

    def get_full_path(self, obj: _VideoDetailLike) -> str | None:
        f = obj.processed_file or obj.raw_file
        return (
            str(Path(settings.MEDIA_ROOT) / cast(str, getattr(f, "name", "")))
            if f
            else None
        )

    def get_duration(self, obj: VideoFile) -> float | None:
        """
        Return the duration of the video, using the stored value if available or calculating it if not.

        Parameters:
            obj (VideoFile): The video file instance.

        Returns:
            float or None: Duration of the video in seconds, or None if unavailable.
        """
        return obj.duration or _calc_duration_vf(obj)

    def get_segment_annotations_validated(self, obj: VideoFile) -> bool:
        return bool(segment_annotations_are_final(obj))

    def get_segment_annotation_status(self, obj: VideoFile) -> str:
        return resolve_segment_annotation_status(obj)

    def get_outside_segments_removed(self, obj: VideoFile) -> bool:
        state = getattr(obj, "state", None)
        if state is None:
            return False
        return bool(getattr(state, "outside_segments_removed", False))

    def get_post_validation_rebuild(self, obj: VideoFile) -> object:
        return post_validation_rebuild_summary(obj)

    def get_anonymization_status(self, obj: _VideoDetailLike) -> str:
        state = getattr(obj, "state", None)
        if state is None:
            return "not_started"
        status = getattr(state, "anonymization_status", "not_started")
        return getattr(status, "value", str(status))

    def get_integrity_status(self, obj: _VideoDetailLike) -> str:
        payload_obj = getattr(obj, "meta", None)
        payload = (
            cast(dict[str, object], payload_obj)
            if isinstance(payload_obj, dict)
            else {}
        )
        status = str(payload.get("integrity_status") or "").strip()
        if status:
            return status
        state = getattr(obj, "state", None)
        if state is not None and getattr(state, "processing_error", False):
            return "lost"
        return ""

    def get_integrity_error(self, obj: _VideoDetailLike) -> str:
        payload_obj = getattr(obj, "meta", None)
        payload = (
            cast(dict[str, object], payload_obj)
            if isinstance(payload_obj, dict)
            else {}
        )
        return str(payload.get("integrity_error") or "").strip()

    def get_patient_dob(self, obj: _VideoDetailLike) -> date | None:
        """
        Returns the patient's date of birth as a date object if available, or None if not present.

        Extracts the date part from the patient's date of birth field in the sensitive metadata, handling both datetime and date types.
        """
        if obj.sensitive_meta and obj.sensitive_meta.patient_dob:
            dob = obj.sensitive_meta.patient_dob
            return dob.date() if isinstance(dob, datetime) else dob
        return None

    def get_examination_date(self, obj: _VideoDetailLike) -> date | None:
        """
        Returns the examination date as a date object from the sensitive metadata, or None if unavailable.

        If the examination date is a datetime, only the date part is returned.
        """
        if obj.sensitive_meta and obj.sensitive_meta.examination_date:
            exam_date = obj.sensitive_meta.examination_date
            return exam_date.date() if isinstance(exam_date, datetime) else exam_date
        return None

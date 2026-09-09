from __future__ import annotations

"""
Video Examination Serializer

Serializes PatientExamination instances that are associated with VideoFile records.
This allows frontend components like VideoExaminationAnnotation.vue to display
and manage examinations within the video annotation workflow.
"""

from datetime import datetime
from typing import Any, cast

from lx_dtypes.models.contracts import (
    VideoExaminationCreateData,
    VideoExaminationCreatePayload,
    VideoExaminationFindingData,
    VideoExaminationFindingPayload,
    VideoExaminationUpdateData,
    VideoExaminationUpdatePayload,
    dump_video_examination_create_payload,
    dump_video_examination_finding_payload,
    dump_video_examination_update_payload,
)
from rest_framework import serializers

from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.utils.pydantic_drf import validate_drf_payload


class VideoExaminationSerializer(serializers.ModelSerializer[PatientExamination]):
    """
    Serializer for video-based patient examinations.

    Exposes examination data within the context of video annotation:
    - Basic examination metadata (type, date, hash)
    - Related patient information (anonymized)
    - Video reference
    - Associated findings
    """

    # Custom fields for frontend compatibility
    examination_name = serializers.CharField(source="examination.name", read_only=True)
    examination_id = serializers.IntegerField(source="examination.id", read_only=True)
    video_id = serializers.SerializerMethodField()
    video_ids = serializers.SerializerMethodField()
    patient_hash = serializers.CharField(source="patient.patient_hash", read_only=True)

    # Nested findings data
    findings = serializers.SerializerMethodField()

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = PatientExamination
        fields = [
            "id",
            "hash",
            "examination_id",
            "examination_name",
            "video_id",
            "video_ids",
            "patient_hash",
            "date_start",
            "date_end",
            "findings",
        ]
        read_only_fields = ["hash", "patient_hash"]

    def get_findings(
        self, obj: PatientExamination
    ) -> list[VideoExaminationFindingData]:
        """
        Return serialized findings associated with this examination.

        Args:
            obj: PatientExamination instance

        Returns:
            List of finding dictionaries with basic metadata
        """
        patient_findings = obj.get_findings().select_related("finding")
        return [
            self._serialize_finding(patient_finding)
            for patient_finding in patient_findings
        ]

    def get_video_ids(self, obj: PatientExamination) -> list[int]:
        return sorted(int(video.pk) for video in obj.video_files.all())

    def get_video_id(self, obj: PatientExamination) -> int | None:
        video_ids = self.get_video_ids(obj)
        return video_ids[0] if len(video_ids) == 1 else None

    @staticmethod
    def _serialize_finding(
        patient_finding: PatientFinding,
    ) -> VideoExaminationFindingData:
        finding = cast(Finding | None, getattr(patient_finding, "finding", None))
        payload = VideoExaminationFindingPayload(
            id=cast(int, patient_finding.pk),
            finding_id=cast(int, finding.pk) if finding is not None else None,
            finding_name=cast(str, getattr(finding, "name"))
            if finding is not None
            else None,
            created_at=cast(
                datetime | None, getattr(patient_finding, "created_at", None)
            ),
        )
        return dump_video_examination_finding_payload(payload)


class VideoExaminationCreateSerializer(serializers.Serializer[PatientExamination]):
    """
    Serializer for creating video examinations via API.

    Handles the complex creation logic required to link:
    - VideoFile (must exist)
    - Examination type (must exist)
    - Patient (derived from video's SensitiveMeta)
    - New PatientExamination record
    """

    video_id = serializers.IntegerField(required=True)
    examination_id = serializers.IntegerField(required=True)
    date_start = serializers.DateField(required=False, allow_null=True)
    date_end = serializers.DateField(required=False, allow_null=True)

    def validate(self, attrs: Any) -> dict[str, Any]:
        payload = validate_drf_payload(VideoExaminationCreatePayload, attrs)
        return cast(dict[str, Any], dump_video_examination_create_payload(payload))

    def validate_video_id(self, value: int) -> int:
        """Ensure video exists"""
        if not VideoFile.objects.filter(id=value).exists():
            raise serializers.ValidationError(f"Video with id {value} does not exist")
        return value

    def validate_examination_id(self, value: int) -> int:
        """Ensure examination type exists"""
        if not Examination.objects.filter(id=value).exists():
            raise serializers.ValidationError(
                f"Examination with id {value} does not exist"
            )
        return value

    def create(self, validated_data: Any) -> PatientExamination:
        """
        Create PatientExamination record.

        Links video to examination through patient relationship:
        1. Get video and extract patient from SensitiveMeta
        2. Get examination type
        3. Create PatientExamination linking patient, examination, video

        Raises:
            ValidationError: If video has no patient or sensitive_meta
        """
        data = cast(VideoExaminationCreateData, validated_data)
        video = VideoFile.objects.get(id=data["video_id"])
        examination = Examination.objects.get(id=data["examination_id"])

        # Get patient from video's sensitive metadata
        sensitive_meta = cast(
            SensitiveMeta | None, getattr(video, "sensitive_meta", None)
        )
        if sensitive_meta is None:
            raise serializers.ValidationError(
                "Video must have sensitive metadata with patient information"
            )

        patient = cast(Patient | None, getattr(sensitive_meta, "pseudo_patient", None))
        if patient is None:
            raise serializers.ValidationError(
                "Video's sensitive metadata must have an associated pseudo patient"
            )

        if video.examination_id is not None:
            patient_exam = PatientExamination.objects.select_for_update().get(
                pk=video.examination_id
            )
            if patient_exam.patient_id != patient.pk:
                raise serializers.ValidationError(
                    {"video_id": "Video is linked to another patient's examination."}
                )
            if (
                patient_exam.examination_id is not None
                and patient_exam.examination_id != examination.pk
            ):
                raise serializers.ValidationError(
                    {
                        "examination_id": (
                            "Examination type does not match the linked patient "
                            "examination."
                        )
                    }
                )
        else:
            patient_exam = PatientExamination.objects.create(
                patient=patient,
                examination=examination,
                date_start=data.get("date_start"),
                date_end=data.get("date_end"),
            )

        video.examination = patient_exam
        video.patient = patient
        video.save(update_fields=["examination", "patient", "date_modified"])

        return patient_exam


class VideoExaminationUpdateSerializer(serializers.Serializer[PatientExamination]):
    """
    Serializer for updating video examinations.

    Allows modification of:
    - Examination type
    - Date range
    - Associated findings (via separate endpoint)
    """

    examination_id = serializers.IntegerField(required=False)
    date_start = serializers.DateField(required=False, allow_null=True)
    date_end = serializers.DateField(required=False, allow_null=True)

    def validate(self, attrs: Any) -> dict[str, Any]:
        payload = validate_drf_payload(VideoExaminationUpdatePayload, attrs)
        return cast(dict[str, Any], dump_video_examination_update_payload(payload))

    def validate_examination_id(self, value: int | None) -> int | None:
        """Ensure examination type exists if provided"""
        if value is not None and not Examination.objects.filter(id=value).exists():
            raise serializers.ValidationError(
                f"Examination with id {value} does not exist"
            )
        return value

    def update(
        self, instance: PatientExamination, validated_data: Any
    ) -> PatientExamination:
        """
        Update PatientExamination fields.

        Args:
            instance: Existing PatientExamination
            validated_data: Validated update data

        Returns:
            Updated PatientExamination instance
        """
        data = cast(VideoExaminationUpdateData, validated_data)
        if "examination_id" in data:
            examination = Examination.objects.get(id=data["examination_id"])
            instance.examination = examination

        if "date_start" in data:
            instance.date_start = data["date_start"]

        if "date_end" in data:
            instance.date_end = data["date_end"]

        cast(Any, instance).save()
        return instance

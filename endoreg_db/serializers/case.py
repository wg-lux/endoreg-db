from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, cast

from django.db import models
from rest_framework import serializers

from endoreg_db.models.administration.case.case import Case
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_lab_sample import PatientLabSample
from endoreg_db.models.medical.patient.patient_lab_value import PatientLabValue
from endoreg_db.models.medical.patient.patient_medication import PatientMedication
from endoreg_db.models.medical.patient.patient_medication_schedule import (
    PatientMedicationSchedule,
)
from endoreg_db.serializers.patient_examination import PatientExaminationSerializer
from endoreg_db.services.cases import CASE_RELATION_FIELDS, persist_case_graph

if TYPE_CHECKING:
    _CaseModelSerializer = serializers.ModelSerializer[Case]
    _ModelSerializerMeta = _CaseModelSerializer.Meta
else:
    _ModelSerializerMeta = object


class CaseSerializer(serializers.ModelSerializer[Case]):
    admission_date = serializers.DateTimeField(source="start_date")
    leave_date = serializers.DateTimeField(
        source="end_date", required=False, allow_null=True
    )
    patient_examinations = PatientExaminationSerializer(many=True, read_only=True)
    documents = serializers.SerializerMethodField()
    patient_medications: serializers.ManyRelatedField = cast(
        serializers.ManyRelatedField,
        serializers.PrimaryKeyRelatedField(many=True, read_only=True),
    )
    patient_medication_schedules: serializers.ManyRelatedField = cast(
        serializers.ManyRelatedField,
        serializers.PrimaryKeyRelatedField(many=True, read_only=True),
    )
    patient_lab_samples: serializers.ManyRelatedField = cast(
        serializers.ManyRelatedField,
        serializers.PrimaryKeyRelatedField(many=True, read_only=True),
    )
    patient_lab_values: serializers.ManyRelatedField = cast(
        serializers.ManyRelatedField,
        serializers.PrimaryKeyRelatedField(many=True, read_only=True),
    )
    patient_examination_ids: serializers.ManyRelatedField = cast(
        serializers.ManyRelatedField,
        serializers.PrimaryKeyRelatedField(
            source="patient_examinations",
            many=True,
            queryset=PatientExamination.objects.all(),
            write_only=True,
            required=False,
        ),
    )
    patient_medication_ids: serializers.ManyRelatedField = cast(
        serializers.ManyRelatedField,
        serializers.PrimaryKeyRelatedField(
            source="patient_medications",
            many=True,
            queryset=PatientMedication.objects.all(),
            write_only=True,
            required=False,
        ),
    )
    patient_medication_schedule_ids: serializers.ManyRelatedField = cast(
        serializers.ManyRelatedField,
        serializers.PrimaryKeyRelatedField(
            source="patient_medication_schedules",
            many=True,
            queryset=PatientMedicationSchedule.objects.all(),
            write_only=True,
            required=False,
        ),
    )
    patient_lab_sample_ids: serializers.ManyRelatedField = cast(
        serializers.ManyRelatedField,
        serializers.PrimaryKeyRelatedField(
            source="patient_lab_samples",
            many=True,
            queryset=PatientLabSample.objects.all(),
            write_only=True,
            required=False,
        ),
    )
    patient_lab_value_ids: serializers.ManyRelatedField = cast(
        serializers.ManyRelatedField,
        serializers.PrimaryKeyRelatedField(
            source="patient_lab_values",
            many=True,
            queryset=PatientLabValue.objects.all(),
            write_only=True,
            required=False,
        ),
    )

    class Meta(_ModelSerializerMeta):
        fields = (
            "id",
            "case_id",
            "patient",
            "admission_date",
            "leave_date",
            "hash",
            "is_active",
            "is_closed",
            "is_deleted",
            "patient_examinations",
            "documents",
            "patient_medications",
            "patient_medication_schedules",
            "patient_lab_samples",
            "patient_lab_values",
            "patient_examination_ids",
            "patient_medication_ids",
            "patient_medication_schedule_ids",
            "patient_lab_sample_ids",
            "patient_lab_value_ids",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "case_id", "created_at", "updated_at")

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        start_date = cast(
            datetime | None,
            attrs.get("start_date", getattr(self.instance, "start_date", None)),
        )
        end_date = cast(
            datetime | None,
            attrs.get("end_date", getattr(self.instance, "end_date", None)),
        )
        if start_date is not None and end_date is not None and end_date < start_date:
            raise serializers.ValidationError(
                {"leave_date": "Leave date must not be earlier than admission date."}
            )
        return attrs

    def get_documents(self, instance: Case) -> list[dict[str, object]]:
        documents: list[dict[str, object]] = []
        for patient_examination in instance.patient_examinations.all():
            patient_examination_id = cast(int, patient_examination.pk)
            for pdf in patient_examination.raw_pdf_files.all():
                documents.append(
                    {
                        "media_type": "pdf",
                        "id": pdf.pk,
                        "uuid": str(pdf.uuid),
                        "patient_examination_id": patient_examination_id,
                        "occurrence_at": pdf.date_created,
                        "file_name": getattr(pdf.file, "name", None),
                    }
                )
            for video in patient_examination.video_files.all():
                documents.append(
                    {
                        "media_type": "video",
                        "id": video.pk,
                        "uuid": str(video.uuid),
                        "patient_examination_id": patient_examination_id,
                        "occurrence_at": video.uploaded_at,
                        "file_name": video.original_file_name,
                    }
                )
            for report in patient_examination.reports.all():
                documents.append(
                    {
                        "media_type": "text_report",
                        "id": cast(int, report.pk),
                        "patient_examination_id": patient_examination_id,
                        "occurrence_at": report.created_at,
                        "title": report.title,
                        "status": report.status,
                        "version": report.version,
                    }
                )
        return sorted(
            documents,
            key=lambda document: (
                cast(datetime, document["occurrence_at"]),
                cast(int, document["id"]),
            ),
            reverse=True,
        )

    def _persist(
        self, instance: Case | None, validated_data: dict[str, object]
    ) -> Case:
        relationships: dict[str, Sequence[models.Model]] = {}
        for field_name in CASE_RELATION_FIELDS:
            if field_name in validated_data:
                relationships[field_name] = cast(
                    Sequence[models.Model], validated_data.pop(field_name)
                )
        try:
            return persist_case_graph(
                instance=instance,
                scalar_values=validated_data,
                relationships=relationships,
            )
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def create(self, validated_data: dict[str, object]) -> Case:
        return self._persist(None, validated_data)

    def update(self, instance: Case, validated_data: dict[str, object]) -> Case:
        if "patient" not in validated_data:
            validated_data["patient"] = instance.patient
        return self._persist(instance, validated_data)


setattr(CaseSerializer.Meta, "model", Case)

from __future__ import annotations

from typing import Any, cast

from lx_dtypes.models.contracts.patient_examination_report import (
    PatientExaminationReportMakeReportData,
    PatientExaminationReportMakeReportPayload,
    PatientExaminationReportSubmissionData,
    PatientExaminationReportSubmissionPayload,
    PatientReportIdentityData,
    PatientReportIdentityPayload,
    dump_make_report_payload,
    dump_report_submission_payload,
)
from rest_framework import serializers

from endoreg_db.models.report.patient_examination_report import PatientExaminationReport
from endoreg_db.utils.pydantic_drf import validate_drf_payload


class PatientExaminationReportSerializer(
    serializers.ModelSerializer[PatientExaminationReport]
):
    dtypes_record = serializers.JSONField(
        source="patient_examination.dtypes_record",
        read_only=True,
    )
    dtypes_record_updated_at = serializers.DateTimeField(
        source="patient_examination.dtypes_record_updated_at",
        read_only=True,
    )

    class Meta:  # type: ignore[reportIncompatibleVariableOverride]
        model = PatientExaminationReport
        fields = [
            "id",
            "patient_examination",
            "template_name",
            "template_version",
            "template_hash",
            "title",
            "status",
            "editor_payload",
            "patient_context_snapshot",
            "history_context_snapshot",
            "rendered_text",
            "dtypes_record",
            "dtypes_record_updated_at",
            "version",
            "is_active",
            "created_at",
            "updated_at",
            "finalized_at",
            "created_by",
            "updated_by",
            "finalized_by",
        ]
        read_only_fields = [
            "id",
            "version",
            "created_at",
            "updated_at",
            "finalized_at",
            "dtypes_record",
            "dtypes_record_updated_at",
            "created_by",
            "updated_by",
            "finalized_by",
        ]


class PatientExaminationReportSubmissionSerializer(
    serializers.Serializer[PatientExaminationReportSubmissionData]
):
    report_id = serializers.IntegerField(required=False)
    patient_examination_id = serializers.IntegerField()
    template_name = serializers.CharField()
    template_version = serializers.CharField(
        required=False, allow_blank=True, default=""
    )
    template_hash = serializers.CharField(required=False, allow_blank=True, default="")
    title = serializers.CharField(required=False, allow_blank=True, default="")
    status = serializers.ChoiceField(
        choices=PatientExaminationReport.Status.choices,
        required=False,
        default=PatientExaminationReport.Status.DRAFT,
    )
    rendered_text = serializers.CharField(required=False, allow_blank=True, default="")
    editor_payload = serializers.DictField(required=False, default=dict)
    patient_data = serializers.DictField(required=False, default=dict)
    indications = serializers.ListField(
        child=serializers.DictField(), required=False, allow_empty=True
    )
    findings = serializers.ListField(
        child=serializers.DictField(), required=False, allow_empty=True
    )
    expected_version = serializers.IntegerField(required=False, min_value=1)
    history_limit = serializers.IntegerField(
        required=False, min_value=1, max_value=50, default=5
    )

    def validate(self, attrs: Any) -> dict[str, Any]:
        payload = validate_drf_payload(PatientExaminationReportSubmissionPayload, attrs)
        return cast(dict[str, Any], dump_report_submission_payload(payload))


class PatientReportIdentitySerializer(
    serializers.Serializer[PatientReportIdentityData]
):
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    dob = serializers.DateField()

    def validate(self, attrs: Any) -> dict[str, Any]:
        payload = validate_drf_payload(PatientReportIdentityPayload, attrs)
        return payload.model_dump(mode="python")


class PatientExaminationReportMakeReportSerializer(
    serializers.Serializer[PatientExaminationReportMakeReportData]
):
    patient_examination_id = serializers.IntegerField()
    report_id = serializers.IntegerField(required=False)
    patient = PatientReportIdentitySerializer()
    max_frames = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=24,
        default=12,
    )

    def validate(self, attrs: Any) -> dict[str, Any]:
        payload = validate_drf_payload(PatientExaminationReportMakeReportPayload, attrs)
        return cast(dict[str, Any], dump_make_report_payload(payload))

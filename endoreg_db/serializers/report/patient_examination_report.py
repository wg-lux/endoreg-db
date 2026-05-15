from __future__ import annotations

from rest_framework import serializers

from endoreg_db.models import PatientExaminationReport


class PatientExaminationReportSerializer(serializers.ModelSerializer):
    class Meta:
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
            "created_by",
            "updated_by",
            "finalized_by",
        ]


class PatientExaminationReportSubmissionSerializer(serializers.Serializer):
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


class PatientReportIdentitySerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    dob = serializers.DateField()


class PatientExaminationReportMakeReportSerializer(serializers.Serializer):
    patient_examination_id = serializers.IntegerField()
    report_id = serializers.IntegerField(required=False)
    patient = PatientReportIdentitySerializer()
    max_frames = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=24,
        default=12,
    )

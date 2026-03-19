from __future__ import annotations

from rest_framework import serializers


class PatientExaminationDraftSerializer(serializers.Serializer):
    module_name = serializers.CharField(required=False, allow_blank=True, default="")
    template_name = serializers.CharField(required=False, allow_blank=True, default="")
    payload = serializers.JSONField(required=False, default=dict)


class PatientExaminationDraftResponseSerializer(serializers.Serializer):
    patient_examination_id = serializers.IntegerField()
    draft = serializers.JSONField()
    updated_at = serializers.DateTimeField(allow_null=True)

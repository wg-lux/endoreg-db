from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from rest_framework import serializers

from endoreg_db.schemas.sensitive_meta_update import SensitiveMetaUpdateCommand


class SensitiveMetaUpdateSerializer(serializers.Serializer[object]):
    """Validate the HTTP representation and build one typed update command."""

    patient_first_name = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=255,
        trim_whitespace=False,
    )
    patient_last_name = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=255,
        trim_whitespace=False,
    )
    patient_dob = serializers.DateTimeField(required=False, allow_null=True)
    examination_date = serializers.DateField(required=False, allow_null=True)
    center_name = serializers.CharField(write_only=True, required=False)
    patient_gender_name = serializers.CharField(write_only=True, required=False)
    endoscope_type = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=255
    )
    endoscope_sn = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=255
    )
    examiner_first_name = serializers.CharField(read_only=True, allow_null=True)
    examiner_last_name = serializers.CharField(read_only=True, allow_null=True)
    dob_verified = serializers.BooleanField(required=False)
    names_verified = serializers.BooleanField(required=False)

    def validate_patient_first_name(self, value: str | None) -> str | None:
        if value and not value.strip():
            raise serializers.ValidationError("First name cannot be empty.")
        return value

    def validate_patient_last_name(self, value: str | None) -> str | None:
        if value and not value.strip():
            raise serializers.ValidationError("Last name cannot be empty.")
        return value

    def to_command(self) -> SensitiveMetaUpdateCommand:
        validated_data = cast(Mapping[str, object], self.validated_data)
        return SensitiveMetaUpdateCommand.model_validate(dict(validated_data))


__all__ = ["SensitiveMetaUpdateSerializer"]

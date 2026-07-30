from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from pydantic import ValidationError as PydanticValidationError
from rest_framework import serializers

from endoreg_db.schemas.report_draft import (
    dump_patient_examination_report_draft,
)


class PatientExaminationDraftSerializer(serializers.Serializer[dict[str, object]]):
    module_name = serializers.CharField(required=False, allow_blank=True, default="")
    template_name = serializers.CharField(required=False, allow_blank=True, default="")
    template_identity = serializers.JSONField(required=False, allow_null=True)
    payload = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        initial_data = cast(object, self.initial_data)
        if not isinstance(initial_data, Mapping):
            raise serializers.ValidationError("Draft payload must be an object.")
        try:
            return cast(
                dict[str, object],
                dump_patient_examination_report_draft(
                    cast(Mapping[str, Any], initial_data)
                ),
            )
        except PydanticValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors(include_url=False, include_input=False)
            )
            raise serializers.ValidationError(details) from exc


class PatientExaminationDraftResponseSerializer(
    serializers.Serializer[dict[str, object]]
):
    patient_examination_id = serializers.IntegerField()
    draft = serializers.JSONField()
    updated_at = serializers.DateTimeField(allow_null=True)

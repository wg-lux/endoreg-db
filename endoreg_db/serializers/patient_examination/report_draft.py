from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from pydantic import ValidationError as PydanticValidationError
from rest_framework import serializers

from endoreg_db.schemas.report_draft import (
    dump_patient_examination_report_draft,
)


class PatientExaminationDraftSerializer(serializers.Serializer[dict[str, object]]):
    expected_revision = serializers.IntegerField(
        required=True, min_value=0, write_only=True
    )
    module_name = serializers.CharField(required=False, allow_blank=True, default="")
    template_name = serializers.CharField(required=False, allow_blank=True, default="")
    template_identity = serializers.JSONField(required=False, allow_null=True)
    indications = serializers.ListField(required=False, default=list)
    template_section_drafts = serializers.JSONField(required=False, default=dict)
    selected_report_language = serializers.ChoiceField(
        choices=("de", "en"), required=False, default="de"
    )
    active_report_id = serializers.IntegerField(
        required=False, allow_null=True, min_value=1
    )
    report_text_mode = serializers.ChoiceField(
        choices=("generated", "manual"), required=False, default="generated"
    )
    rendered_text = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=False, default=""
    )
    payload = serializers.JSONField(required=False, default=dict)
    validated_expected_revision: int

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        initial_data = cast(object, self.initial_data)
        if not isinstance(initial_data, Mapping):
            raise serializers.ValidationError("Draft payload must be an object.")
        expected_revision = attrs.pop("expected_revision")
        if not isinstance(expected_revision, int):
            raise serializers.ValidationError("expected_revision must be an integer.")
        self.validated_expected_revision = expected_revision
        draft_data = dict(cast(Mapping[str, object], initial_data))
        draft_data.pop("expected_revision", None)
        try:
            return cast(
                dict[str, object],
                dump_patient_examination_report_draft(
                    cast(Mapping[str, Any], draft_data)
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
    revision = serializers.IntegerField(min_value=0)
    draft = serializers.JSONField()
    updated_at = serializers.DateTimeField(allow_null=True)

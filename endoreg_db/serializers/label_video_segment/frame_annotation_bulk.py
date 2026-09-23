# pyright: reportIncompatibleMethodOverride=false, reportUnusedClass=false
from __future__ import annotations

from typing import TypedDict

from pydantic import ValidationError as PydanticValidationError
from rest_framework import serializers

from endoreg_db.models.label.label import Label
from lx_dtypes.models.contracts.video_frame_annotations import (
    FrameAnnotationBulkItemData,
    FrameAnnotationBulkItemPayload,
    dump_frame_annotation_bulk_item,
)


class _FrameAnnotationBulkItemAttrs(TypedDict, total=False):
    frame_id: int
    label_id: int | None
    choice_name: str | None
    value: bool
    float_value: float | None
    information_source_name: str
    annotator: str | None
    external_annotation_id: str | None
    model_meta_id: int | None


class FrameAnnotationBulkItemSerializer(
    serializers.Serializer[FrameAnnotationBulkItemData]
):
    """
    Payload item for bulk frame annotation upsert.
    """

    frame_id = serializers.IntegerField()
    label_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    choice_name = serializers.CharField(required=False, allow_blank=True)
    value = serializers.BooleanField(required=False, default=True)
    float_value = serializers.FloatField(required=False, allow_null=True)
    information_source_name = serializers.CharField(max_length=100)
    annotator = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    external_annotation_id = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    model_meta_id = serializers.IntegerField(required=False, allow_null=True)

    @staticmethod
    def _parse_choice_name(choice_name: str) -> tuple[str, bool | None]:
        normalized = choice_name.strip()
        lowered = normalized.lower()
        if lowered.endswith(": present"):
            return normalized[: -len(": present")].strip(), True
        if lowered.endswith(": absent"):
            return normalized[: -len(": absent")].strip(), False
        return normalized, None

    def validate(
        self, attrs: _FrameAnnotationBulkItemAttrs
    ) -> FrameAnnotationBulkItemData:
        payload_data = attrs.copy()
        label_id = payload_data.get("label_id")
        choice_name_value = payload_data.get("choice_name")
        choice_name = choice_name_value.strip() if choice_name_value is not None else ""

        if label_id is None and not choice_name:
            raise serializers.ValidationError(
                {"label_id": "Either label_id or choice_name is required."}
            )

        if label_id is not None:
            if not Label.objects.filter(pk=label_id).exists():
                raise serializers.ValidationError(
                    {"label_id": f"Unknown label id: {label_id}"}
                )
        else:
            label_name, inferred_value = self._parse_choice_name(choice_name)
            if not label_name:
                raise serializers.ValidationError(
                    {"choice_name": "choice_name must include a non-empty label name."}
                )

            label = Label.objects.filter(name=label_name).only("id").first()
            if label is None:
                label = Label.objects.filter(name__iexact=label_name).only("id").first()
            if label is None:
                raise serializers.ValidationError(
                    {"choice_name": f"Unknown label name: {label_name}"}
                )

            label_pk = label.pk
            if not isinstance(label_pk, int):
                raise serializers.ValidationError(
                    {"choice_name": f"Unknown label name: {label_name}"}
                )

            payload_data["label_id"] = label_pk
            if inferred_value is not None:
                payload_data["value"] = inferred_value

        try:
            payload = FrameAnnotationBulkItemPayload.model_validate(payload_data)
        except PydanticValidationError as exc:
            raise serializers.ValidationError({"payload": str(exc)}) from exc

        return dump_frame_annotation_bulk_item(payload)

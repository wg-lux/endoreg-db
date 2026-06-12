import math
from typing import Any, cast, TYPE_CHECKING

from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object

from endoreg_db.models.label.annotation.frame_box import FrameBoxAnnotation
from endoreg_db.models.label.label import Label


class FrameBoxAnnotationSerializer(serializers.ModelSerializer[FrameBoxAnnotation]):
    label_name = serializers.CharField(source="label.name", read_only=True)
    information_source_name = serializers.CharField(
        source="information_source.name",
        read_only=True,
    )
    frame_id = serializers.IntegerField(read_only=True)
    label_id = serializers.IntegerField(read_only=True)
    model_meta_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta(_ModelSerializerMeta):
        model = FrameBoxAnnotation  # pyright: ignore[reportAssignmentType]
        fields = [
            "id",
            "frame_id",
            "label_id",
            "label_name",
            "value",
            "float_value",
            "x",
            "y",
            "width",
            "height",
            "image_width",
            "image_height",
            "annotator",
            "external_annotation_id",
            "model_meta_id",
            "information_source_name",
            "date_created",
            "date_modified",
        ]


class FrameBoxAnnotationBulkItemSerializer(serializers.Serializer[dict[str, object]]):
    """
    Payload item for general frame box annotation persistence.
    """

    id = serializers.IntegerField(required=False, allow_null=True)
    frame_id = serializers.IntegerField()
    label_id = serializers.IntegerField(required=False, allow_null=True)
    choice_name = serializers.CharField(required=False, allow_blank=True)
    value = serializers.BooleanField(required=False, default=True)
    float_value = serializers.FloatField(required=False, allow_null=True)
    x = serializers.FloatField()
    y = serializers.FloatField()
    width = serializers.FloatField()
    height = serializers.FloatField()
    image_width = serializers.IntegerField()
    image_height = serializers.IntegerField()
    information_source_name = serializers.CharField(max_length=100)
    annotator = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    external_annotation_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
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

    @staticmethod
    def _finite_float(attrs: dict[str, Any], field_name: str) -> float:
        value = float(attrs[field_name])
        if not math.isfinite(value):
            raise serializers.ValidationError(
                {field_name: f"{field_name} must be finite."}
            )
        return value

    @staticmethod
    def _positive_int(value: object, field_name: str) -> int:
        if isinstance(value, bool):
            raise serializers.ValidationError(
                {field_name: f"{field_name} must be an integer."}
            )
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip():
            return int(value.strip())
        raise serializers.ValidationError(
            {field_name: f"{field_name} must be an integer."}
        )

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        label_id = attrs.get("label_id")
        choice_name = str(attrs.get("choice_name", "") or "").strip()

        if label_id is None and not choice_name:
            raise serializers.ValidationError(
                {"label_id": "Either label_id or choice_name is required."}
            )

        if label_id is None:
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

            attrs["label_id"] = cast(int, label.pk)
            if inferred_value is not None:
                attrs["value"] = inferred_value

        x = self._finite_float(attrs, "x")
        y = self._finite_float(attrs, "y")
        width = self._finite_float(attrs, "width")
        height = self._finite_float(attrs, "height")
        image_width = self._positive_int(attrs["image_width"], "image_width")
        image_height = self._positive_int(attrs["image_height"], "image_height")

        if image_width <= 0:
            raise serializers.ValidationError(
                {"image_width": "image_width must be greater than 0."}
            )
        if image_height <= 0:
            raise serializers.ValidationError(
                {"image_height": "image_height must be greater than 0."}
            )
        if x < 0:
            raise serializers.ValidationError({"x": "x must be >= 0."})
        if y < 0:
            raise serializers.ValidationError({"y": "y must be >= 0."})
        if width <= 0:
            raise serializers.ValidationError({"width": "width must be > 0."})
        if height <= 0:
            raise serializers.ValidationError({"height": "height must be > 0."})
        if x + width > image_width:
            raise serializers.ValidationError(
                {"width": "x + width must not exceed image_width."}
            )
        if y + height > image_height:
            raise serializers.ValidationError(
                {"height": "y + height must not exceed image_height."}
            )

        attrs["x"] = x
        attrs["y"] = y
        attrs["width"] = width
        attrs["height"] = height
        attrs["image_width"] = image_width
        attrs["image_height"] = image_height
        return attrs

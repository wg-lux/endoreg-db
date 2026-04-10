from rest_framework import serializers

from endoreg_db.models import Label


class FrameAnnotationBulkItemSerializer(serializers.Serializer):
    """
    Payload item for bulk frame annotation upsert.
    """

    frame_id = serializers.IntegerField()
    label_id = serializers.ChoiceField(choices=(), required=False, allow_null=True)
    choice_name = serializers.CharField(required=False, allow_blank=True)
    value = serializers.BooleanField(required=False, default=True)
    float_value = serializers.FloatField(required=False, allow_null=True)
    information_source_name = serializers.CharField(max_length=100)
    annotator = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    external_annotation_id = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    model_meta_id = serializers.IntegerField(required=False, allow_null=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["label_id"].choices = [
            (label.pk, label.name) for label in Label.objects.all().order_by("name")
        ]

    @staticmethod
    def _parse_choice_name(choice_name: str) -> tuple[str, bool | None]:
        normalized = choice_name.strip()
        lowered = normalized.lower()
        if lowered.endswith(": present"):
            return normalized[: -len(": present")].strip(), True
        if lowered.endswith(": absent"):
            return normalized[: -len(": absent")].strip(), False
        return normalized, None

    def validate(self, attrs):
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

            attrs["label_id"] = label.id
            if inferred_value is not None:
                attrs["value"] = inferred_value

        return attrs

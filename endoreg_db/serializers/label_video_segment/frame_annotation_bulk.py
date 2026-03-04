from rest_framework import serializers

from endoreg_db.models import Label


class FrameAnnotationBulkItemSerializer(serializers.Serializer):
    """
    Payload item for bulk frame annotation upsert.
    """

    frame_id = serializers.IntegerField()
    label_id = serializers.ChoiceField(choices=())
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

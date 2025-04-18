from rest_framework import serializers
from endoreg_db.models import VideoMeta, EndoscopyProcessor

from .endoscopy_processor import EndoscopyProcessorSerializer
from .endoscope import EndoscopeSerializer

class VideoMetaSerializer(serializers.ModelSerializer):
    """Serializer for VideoMeta representation."""

    def __init__(self, *args, **kwargs):
        # Pop custom arguments *before* calling super
        endoscopy_processor_full = kwargs.pop("endoscopy_processor_full", False)
        endoscopy_processor_name = kwargs.pop("endoscopy_processor_name", False)

        # Call super().__init__ *after* popping custom args
        super().__init__(*args, **kwargs)

        # Conditionally set the 'processor' field *after* initialization
        if endoscopy_processor_full:
            self.fields["processor"] = EndoscopyProcessorSerializer()
        elif endoscopy_processor_name:
            self.fields["processor"] = serializers.CharField(source="processor.name")
        else:
            self.fields["processor"] = serializers.PrimaryKeyRelatedField(
                queryset=EndoscopyProcessor.objects.all(),
            )

    class Meta:
        model = VideoMeta
        fields = [
            "endoscope",
            "processor",
        ]

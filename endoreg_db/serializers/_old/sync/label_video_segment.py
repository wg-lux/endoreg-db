from rest_framework import serializers
from endoreg_db.models import LabelVideoSegment
# from .video import VideoSerializer
# from .patient_finding import PatientFindingSerializer
from .label import LabelSerializer
# from .prediction_meta import VideoPredictionMetaSerializer
from .source import InformationSourceSerializer

class LabelVideoSegmentSerializer(serializers.ModelSerializer):
    """Serializer for LabelVideoSegment representation."""
    label = LabelSerializer(many=False, read_only=True)
    source = InformationSourceSerializer(many=False, read_only=True)

    class Meta:
        model = LabelVideoSegment
        fields = [
            "start_frame_number",
            "end_frame_number",
            "source",
            "label",
        ]
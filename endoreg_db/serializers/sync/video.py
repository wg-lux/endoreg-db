from rest_framework import serializers
from endoreg_db.models import Video

from .patient import PatientSerializer
from .sensitive_meta import SensitiveMetaSerializer

#######
PATIENT_FULL = True
SENSITIVE_META_FULL = True
#######

class VideoSerializer(serializers.ModelSerializer):
    """Serializer for Video representation."""
    if PATIENT_FULL:
        patient = PatientSerializer()
    else:
        patient = serializers.PrimaryKeyRelatedField(
            queryset=Video.objects.all(),
            source="patient",
        )

    if SENSITIVE_META_FULL:
        sensitive_meta = SensitiveMetaSerializer()
    else:
        sensitive_meta = serializers.PrimaryKeyRelatedField(
            queryset=Video.objects.all(),
            source="sensitive_meta",
        )

    class Meta:
        model = Video
        fields = [
            "patient",
            "sensitive_meta",
        ]
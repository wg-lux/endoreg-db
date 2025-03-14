from rest_framework import serializers
from ..models import RawVideoFile, SensitiveMeta

class VideoFileForMetaSerializer(serializers.ModelSerializer):
    """
    Serializer to fetch video metadata along with linked `SensitiveMeta` details.
    """

    patient_first_name = serializers.CharField(source="sensitive_meta.patient_first_name", read_only=True)

    class Meta:
        model = RawVideoFile
        fields = ['id', 'original_file_name', 'file', 'sensitive_meta_id', 'patient_first_name']

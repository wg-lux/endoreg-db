from rest_framework import serializers
from endoreg_db.models import Center
from .endoscopy_processor import EndoscopyProcessorSerializer
class CenterSerializer(serializers.ModelSerializer):
    """Serializer for Center representation."""

    # Add many=True to handle the one-to-many relationship correctly
    endoscopy_processors = EndoscopyProcessorSerializer(many=True, read_only=True) # Assuming read_only is desired for nested serialization

    class Meta:
        model = Center
        fields = ['name', "name_de", "endoscopy_processors"]

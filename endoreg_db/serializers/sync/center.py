
from rest_framework import serializers
from endoreg_db.models import Center

class ExportCenterSerializer(serializers.ModelSerializer):
    """Serializer for Center representation."""
    class Meta:
        model = Center
        fields = ['name', "name_de"]

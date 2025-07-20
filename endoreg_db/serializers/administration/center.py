from rest_framework import serializers
from endoreg_db.models import Center

class CenterSerializer(serializers.ModelSerializer):
    """Serializer für Center-Modell"""
    class Meta:
        model = Center
        fields = ['id', 'name', 'name_de', 'name_en']
        read_only_fields = ['id']

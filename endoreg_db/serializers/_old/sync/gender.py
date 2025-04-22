
from rest_framework import serializers
from endoreg_db.models import Gender

class ExportGenderSerializer(serializers.ModelSerializer):
    """Serializer for Gender"""
    class Meta:
        model: Gender
        fields = ['name', "name_de", "abbreviation"]

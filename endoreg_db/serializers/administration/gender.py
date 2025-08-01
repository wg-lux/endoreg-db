from rest_framework import serializers
from endoreg_db.models import Gender

class GenderSerializer(serializers.ModelSerializer):
    """Serializer für Gender-Modell"""
    class Meta:
        model = Gender
        fields = ['id', 'name', 'abbreviation', 'description']
        read_only_fields = ['id']

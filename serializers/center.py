from rest_framework import serializers
from agl_base_db.models import (
    Center,
)

class CenterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Center
        fields = '__all__'



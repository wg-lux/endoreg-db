from rest_framework import serializers
from agl_base_db.models import Frame, LegacyFrame

class FrameSerializer(serializers.ModelSerializer):
    class Meta:
        model = Frame
        fields = '__all__'

class LegacyFrameSerializer(serializers.ModelSerializer):
    class Meta:
        model = LegacyFrame
        fields = '__all__'


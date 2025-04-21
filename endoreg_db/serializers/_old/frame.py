from rest_framework import serializers
from endoreg_db.models import Frame

class FrameSerializer(serializers.ModelSerializer):
    class Meta:
        model = Frame
        fields = '__all__'



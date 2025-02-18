from rest_framework import serializers
from endoreg_db.models import (
    Label,
    LabelSet,
    LabelType,
)

class LabelTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabelType
        fields = '__all__'

class LabelSetSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabelSet
        fields = '__all__'

class LabelSerializer(serializers.ModelSerializer):
    class Meta:
        model = Label
        fields = '__all__'
        

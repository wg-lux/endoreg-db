# endoreg_db/serializers.py
from rest_framework import serializers
from ..models import Examination



class ExaminationSerializer(serializers.ModelSerializer):
        class Meta:
            model = Examination
            fields = ['id', 'name']  # Add other fields as needed



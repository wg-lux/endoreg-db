from rest_framework import serializers
from endoreg_db.models import (
    Examination,
    ExaminationTimeType,
    ExaminationTime,
    ExaminationType,
    PatientExamination
)

class ExaminationTimeTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExaminationTimeType
        fields = '__all__'

class ExaminationTimeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExaminationTime
        fields = '__all__'

class ExaminationTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExaminationType
        fields = '__all__'

class ExaminationSerializer(serializers.ModelSerializer): 
    class Meta:
        model = Examination
        fields = '__all__'

class PatientExaminationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PatientExamination
        fields = '__all__'
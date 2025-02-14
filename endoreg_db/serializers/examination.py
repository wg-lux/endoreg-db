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


    #It allows the API to accept & return only the IDs of ExaminationType objects.
    examination_types = serializers.PrimaryKeyRelatedField(
        queryset = ExaminationType.objects.all(),
        many = True
    )
    class Meta:
        model = ExaminationType
        fields = '__all__'
        read_only_fields = ['id']

    def create(self, validated_data):

        examination_types = validated_data.pop('examination_types',[])
        examination = Examination.objects.create(**validated_data)
        examination.examination_types.set(examination_types)
        return examination
    
    def update(self, instance, validated_data):

        examination_types = validated_data.pop('examination_types',None)
        for attr, value in validated_data.items():
            setattr(instance,attr,value)
        if examination_types is not None:
            instance.examination_types.set(examination_types)
        instance.save()
        return instance

class ExaminationSerializer(serializers.ModelSerializer): 
    class Meta:
        model = Examination
        fields = '__all__'

class PatientExaminationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PatientExamination
        fields = '__all__'
from rest_framework import serializers
from endoreg_db.models import Patient, Gender

class PatientSerializer(serializers.ModelSerializer):
    # Use the slug field "name" so that the gender is represented by its string value
    gender = serializers.SlugRelatedField(
        slug_field='name',
        queryset=Gender.objects.all()
    )

    class Meta:
        model = Patient
        fields = '__all__'
        read_only_fields = ['id']

    def get_age(self, obj):
        return obj.age()

    def create(self, validated_data):
        center = validated_data.pop('center', None)
        gender = validated_data.pop('gender', None)
        patient = Patient.objects.create(center=center, gender=gender, **validated_data)
        return patient

    def update(self, instance, validated_data):
        center = validated_data.pop('center', None)
        gender = validated_data.pop('gender', None)
        instance.center = center if center else instance.center
        instance.gender = gender if gender else instance.gender
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance

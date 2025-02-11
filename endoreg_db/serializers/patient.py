from rest_framework import serializers
from endoreg_db.models import Patient, Center, Gender

class GenderSerializer(serializers.ModelSerializer):
    """Serializer for Gender model."""
    class Meta:
        model = Gender
        fields = ['id', 'name']

class PatientSerializer(serializers.ModelSerializer):
    """Serializer for Patient model."""
    age = serializers.SerializerMethodField()
    center = serializers.PrimaryKeyRelatedField(
        queryset=Center.objects.all(), required=False, allow_null=True
    )
    gender = GenderSerializer()  # Nested serializer for GET requests
    gender_id = serializers.PrimaryKeyRelatedField(
        queryset=Gender.objects.all(),
        source="gender",  # Maps to the `gender` ForeignKey
        write_only=True  # Only used for writing, not included in GET response
    )

    class Meta:
        model = Patient
        fields = '__all__'
        read_only_fields = ['id']  # Ensure 'id' is not required when creating

    def get_age(self, obj):
        """Return the calculated age of the patient."""
        return obj.age()

    def create(self, validated_data):
        """Custom create method to handle foreign keys properly."""
        center = validated_data.pop('center', None)
        gender = validated_data.pop('gender', None)

        patient = Patient.objects.create(center=center, gender=gender, **validated_data)
        return patient

    def update(self, instance, validated_data):
        """Custom update method."""
        center = validated_data.pop('center', None)
        gender = validated_data.pop('gender', None)

        instance.center = center if center else instance.center
        instance.gender = gender if gender else instance.gender

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance
            
        


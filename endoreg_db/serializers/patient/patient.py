from rest_framework import serializers
from endoreg_db.models import Patient, Gender, Center
from datetime import date

class PatientSerializer(serializers.ModelSerializer):
    # Use the slug field "name" so that the gender is represented by its string value
    gender = serializers.SlugRelatedField(
        slug_field='name',
        queryset=Gender.objects.all(),
        required=False,
        allow_null=True
    )
    center = serializers.SlugRelatedField(
        slug_field='name',
        queryset=Center.objects.all(),
        required=False,
        allow_null=True
    )
    age = serializers.SerializerMethodField()

    class Meta:
        model = Patient
        fields = ['id', 'first_name', 'last_name', 'dob', 'gender', 'center', 
                 'email', 'phone', 'patient_hash', 'is_real_person', 'age']
        read_only_fields = ['id', 'age']

    def get_age(self, obj):
        """Berechnet das Alter des Patienten"""
        if obj.dob:
            return obj.age()
        return None

    def validate_first_name(self, value):
        """Validiert den Vornamen"""
        if not value or not value.strip():
            raise serializers.ValidationError("Vorname ist erforderlich")
        return value.strip()

    def validate_last_name(self, value):
        """Validiert den Nachnamen"""
        if not value or not value.strip():
            raise serializers.ValidationError("Nachname ist erforderlich")
        return value.strip()

    def validate_dob(self, value):
        """Validiert das Geburtsdatum"""
        if value and value > date.today():
            raise serializers.ValidationError("Geburtsdatum kann nicht in der Zukunft liegen")
        return value

    def validate_email(self, value):
        """Validiert die E-Mail-Adresse"""
        if value and '@' not in value:
            raise serializers.ValidationError("Ungültige E-Mail-Adresse")
        return value

    def create(self, validated_data):
        """Erstellt einen neuen Patienten mit verbesserter Fehlerbehandlung"""
        try:
            patient = Patient.objects.create(**validated_data)
            return patient
        except Exception as e:
            raise serializers.ValidationError(f"Fehler beim Erstellen des Patienten: {str(e)}")

    def update(self, instance, validated_data):
        """
        Update an existing Patient instance with validated data.
        
        Parameters:
            instance (Patient): The Patient instance to update.
            validated_data (dict): Dictionary of validated data to update the instance with.
        
        Returns:
            Patient: The updated Patient instance.
        
        Raises:
            ValidationError: If an error occurs during the update process.
        """
        try:
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()
            return instance
        except Exception as e:
            raise serializers.ValidationError(f"Fehler beim Aktualisieren des Patienten: {str(e)}")


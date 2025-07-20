from endoreg_db.models import Patient


from rest_framework import serializers


class PatientDropdownSerializer(serializers.ModelSerializer):
    """Serializer für Patient-Dropdown"""
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = Patient
        fields = ['id', 'patient_hash', 'first_name', 'last_name', 'display_name', 'dob']

    def get_display_name(self, obj):
        """Gibt eine benutzerfreundliche Anzeige für den Patienten zurück"""
        return f"{obj.first_name} {obj.last_name} ({obj.patient_hash[:8]}...)"
    
from endoreg_db.models import Patient


from rest_framework import serializers


class PatientDropdownSerializer(serializers.ModelSerializer):
    """Serializer für Patient-Dropdown"""
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = Patient
        fields = ['id', 'patient_hash', 'first_name', 'last_name', 'display_name', 'dob']

    def get_display_name(self, obj):
        """
        Returns a user-friendly display string for a patient, combining their first and last name with a shortened patient hash or a placeholder if the hash is missing.
        
        Parameters:
            obj: The patient instance being serialized.
        
        Returns:
            str: The formatted display name for the patient.
        """
        patient_hash = obj.patient_hash
        hash_display = f"({patient_hash[:8]}...)" if patient_hash else "(No Hash)"
        return f"{obj.first_name} {obj.last_name} {hash_display}"

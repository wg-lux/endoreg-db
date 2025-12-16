"""
Serializers für Anonymisierungs-Validierung mit deutschem Datumsformat.

Unterstützt DD.MM.YYYY als Primärformat und YYYY-MM-DD als Fallback.
"""

from rest_framework import serializers
from endoreg_db.models.metadata.sensitive_meta_logic import parse_any_date

class SensitiveMetaValidateSerializer(serializers.Serializer):
    """
    Serializer für SensitiveMeta-Validierung mit deutscher Datums-Priorität.
    
    Akzeptiert Datumsfelder in folgenden Formaten:
    1. DD.MM.YYYY (bevorzugt) - deutsches Format
    2. YYYY-MM-DD (Fallback) - ISO-Format
    
    Alle Datumsfelder werden in date-Objekte konvertiert.
    """
    
    patient_first_name = serializers.CharField(required=True, allow_blank=True)
    patient_last_name = serializers.CharField(required=True, allow_blank=True)
    patient_dob = serializers.CharField(required=True, allow_blank=True)
    examination_date = serializers.CharField(required=True, allow_blank=True)
    casenumber = serializers.CharField(required=True, allow_blank=True)
    anonymized_text = serializers.CharField(required=False, allow_blank=True)
    patient_gender = serializers.CharField(required=False, allow_blank=True)
    center_name = serializers.CharField(required=False, allow_blank=True)
    is_verified = serializers.BooleanField(required=False, default=True)
    file_type = serializers.ChoiceField(
        choices=['video', 'pdf'], required=False
    )  # Optional: "video" oder "pdf"
    center_name = serializers.CharField(required=False, allow_blank=True)
    external_id = serializers.CharField(required=False, allow_blank=True)
    external_id_origin = serializers.CharField(required=False, allow_blank=True)
    

    def validate_patient_dob(self, value):
        """
        Validiert patient_dob mit deutscher Format-Priorität.
        
        Akzeptierte Formate:
        - DD.MM.YYYY (z.B. "21.03.1994")
        - YYYY-MM-DD (z.B. "1994-03-21")
        """

        if value == '' or not value:
            raise serializers.ValidationError(
                "Ungültiges Datum. Kein Input! Erlaubte Formate: DD.MM.YYYY oder YYYY-MM-DD."
            ) 
            
        parsed_date = parse_any_date(value)
        if not parsed_date:
            raise serializers.ValidationError(
                "Ungültiges Datum. Erlaubte Formate: DD.MM.YYYY oder YYYY-MM-DD."
            )
        return parsed_date

    def validate_examination_date(self, value):
        """
        Validiert examination_date mit deutscher Format-Priorität.
        
        Akzeptierte Formate:
        - DD.MM.YYYY (z.B. "15.02.2024")
        - YYYY-MM-DD (z.B. "2024-02-15")
        """
        if value == '' or not value:
            raise serializers.ValidationError(
                "Ungültiges Datum. Kein Input! Erlaubte Formate: DD.MM.YYYY oder YYYY-MM-DD."
            ) 
            
        parsed_date = parse_any_date(value)
        if not parsed_date:
            raise serializers.ValidationError(
                "Ungültiges Datum. Erlaubte Formate: DD.MM.YYYY oder YYYY-MM-DD."
            )
        return parsed_date

from endoreg_db.models import SensitiveMeta


from django.utils import timezone
from rest_framework import serializers


class ReportMetaSerializer(serializers.ModelSerializer):
    """
    Serializer für Report-Metadaten basierend auf SensitiveMeta
    """
    # Füge fehlende Zeitstempel-Felder hinzu
    created_at = serializers.SerializerMethodField()
    updated_at = serializers.SerializerMethodField()
    casenumber = serializers.CharField(source='case_number', allow_blank=True, allow_null=True)

    class Meta:
        model = SensitiveMeta
        fields = [
            'id', 'patient_first_name', 'patient_last_name',
            'patient_gender', 'patient_dob', 'examination_date',
            'casenumber', 'created_at', 'updated_at'
        ]

    def get_created_at(self, obj):
        """
        Retrieve the `created_at` timestamp from the SensitiveMeta instance, or return the current time if unavailable.
        
        Parameters:
            obj: The SensitiveMeta instance being serialized.
        
        Returns:
            datetime: The `created_at` timestamp, or the current time as a fallback.
        """
        if hasattr(obj, 'created_at') and obj.created_at:
            return obj.created_at
        # Fallback wenn SensitiveMeta kein created_at hat
        return timezone.now()

    def get_updated_at(self, obj):
        """
        Return the `updated_at` timestamp from the SensitiveMeta instance, or fall back to the `created_at` timestamp if `updated_at` is missing or null.
        
        Parameters:
            obj: The SensitiveMeta instance being serialized.
        
        Returns:
            A datetime representing when the instance was last updated, or created if no update timestamp is available.
        """
        if hasattr(obj, 'updated_at') and obj.updated_at:
            return obj.updated_at
        # Fallback wenn SensitiveMeta kein updated_at hat  
        return self.get_created_at(obj)